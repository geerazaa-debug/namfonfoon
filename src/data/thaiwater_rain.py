"""Live ThaiWater rain: hourly snapshot client + rolling 6 h accumulator.

`public/rain_24h` returns, per station, the latest hour's `rain_1h` and `rain_24h`, stamped
with `rainfall_datetime` (local UTC+7, hour-aligned). The 6 h total is built by keeping each
station's hourly values across polls. Licence of ThaiWater data is not stated - no B2B resale.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Self

import httpx
from pydantic import BaseModel, Field

from src.config import settings
from src.data.db import Database
from src.data.validator import BoundingBox, GeoPoint
from src.engine.geofence import haversine_km
from src.engine.risk_analyzer import RainObservation
from src.retry import request_with_retry

BKK_TZ = timezone(timedelta(hours=7))


class RainSnapshot(BaseModel):
    station_id: str
    location: GeoPoint
    observed_at: datetime  # end of the 1 h accumulation window
    rain_1h_mm: float = Field(ge=0)


def parse_rain_24h(payload: dict[str, Any], bbox: BoundingBox) -> list[RainSnapshot]:
    out = []
    for r in payload.get("data", []):
        st = r.get("station") or {}
        lat, lon, rain = st.get("tele_station_lat"), st.get("tele_station_long"), r.get("rain_1h")
        if lat is None or lon is None or rain is None or not r.get("rainfall_datetime"):
            continue
        p = GeoPoint(lat=float(lat), lon=float(lon))
        if not bbox.contains(p) or float(rain) < 0:
            continue
        t = datetime.strptime(r["rainfall_datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=BKK_TZ)
        out.append(RainSnapshot(station_id=str(st["id"]), location=p, observed_at=t, rain_1h_mm=float(rain)))
    return out


class ThaiWaterRainClient:
    def __init__(self, *, cfg: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.cfg = cfg or settings()["thaiwater"]
        self._client = httpx.AsyncClient(timeout=self.cfg["timeout_s"], transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def latest(self, bbox: BoundingBox) -> list[RainSnapshot]:
        resp = await request_with_retry(self._client, "GET", self.cfg["rain_url"], max_retries=self.cfg["max_retries"])
        return parse_rain_24h(resp.json(), bbox)


class RainAccumulator:
    """Per-station hourly rain history in SQLite (`rain_logs`), so 6 h totals survive restarts."""

    def __init__(
        self,
        cfg: dict[str, Any] | None = None,
        db: Database | None = None,
        district_of: Callable[[GeoPoint], str | None] = lambda _: None,
    ) -> None:
        cfg = cfg or settings()["rain_pipeline"]
        self.radius_km = float(cfg["station_radius_km"])
        self.fallback_km = float(cfg["fallback_radius_km"])
        self.min_hours = int(cfg["min_hours_for_6h"])
        self.keep = timedelta(hours=cfg["history_hours"])
        self.db = db or Database()
        self.district_of = district_of

    def add(self, snaps: list[RainSnapshot]) -> None:
        if not snaps:
            return
        self.db.upsert_rain(  # re-polls of the same station-hour overwrite, never double count
            [
                (
                    s.station_id,
                    s.observed_at,
                    s.rain_1h_mm,
                    s.location.lat,
                    s.location.lon,
                    self.district_of(s.location),
                )
                for s in snaps
            ]
        )
        self.db.prune_rain(max(s.observed_at for s in snaps) - self.keep)

    def observation(self, station_id: str) -> RainObservation | None:
        latest = self.db.latest_rain_time(station_id)
        if latest is None:
            return None
        hours = self.db.rain_hours(station_id, since=latest - timedelta(hours=5))
        return RainObservation(
            source_id=f"thaiwater:{station_id}",
            observed_at=latest,
            rain_1h_mm=hours[latest],
            rain_6h_mm=sum(hours.values()) if len(hours) >= self.min_hours else None,
        )

    def near(self, p: GeoPoint) -> list[RainObservation]:
        where = self.db.station_locations(since=datetime(1970, 1, 1, tzinfo=UTC))
        dist = sorted((haversine_km(p, loc), sid) for sid, loc in where.items())
        chosen = [sid for d, sid in dist if d <= self.radius_km]
        if not chosen and dist and dist[0][0] <= self.fallback_km:
            chosen = [dist[0][1]]
        return [o for sid in chosen if (o := self.observation(sid)) is not None]
