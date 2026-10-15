"""River and canal water levels near Bangkok from ThaiWater `public/waterlevel_load` (~1.4 MB, ~10-min telemetry).

`storage_percent` is the level as % of bank height and `situation_level` (1-5) is ThaiWater's own status,
so the thresholds are theirs: 1 critically low, 2 low, 3 normal, 4 high, 5 over the bank.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.config import settings
from src.data.validator import BoundingBox, GeoPoint
from src.retry import request_with_retry

BKK_TZ = timezone(timedelta(hours=7))


class RiverLevel(BaseModel):
    id: int = Field(ge=1, le=5)
    emoji: str
    label: str
    color: str


@lru_cache(maxsize=1)
def river_levels() -> dict[int, RiverLevel]:
    return {lv["id"]: RiverLevel(**lv) for lv in settings()["river_level"]["levels"]}


class RiverStation(BaseModel):
    code: str
    name: str
    area: str  # province / district text from ThaiWater
    location: GeoPoint
    observed_at: datetime
    bank_percent: float
    situation: int = Field(ge=1, le=5)
    rising: bool | None  # vs the previous reading; None when unknown or flat


def parse_waterlevel(payload: dict[str, Any], bbox: BoundingBox) -> list[RiverStation]:
    out = []
    for r in (payload.get("waterlevel_data") or {}).get("data", []):
        st, geo = r.get("station") or {}, r.get("geocode") or {}
        try:
            p = GeoPoint(lat=float(st["tele_station_lat"]), lon=float(st["tele_station_long"]))
            pct, sit = float(r["storage_percent"]), int(r["situation_level"])
            t = datetime.strptime(r["waterlevel_datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=BKK_TZ).astimezone(UTC)
        except (KeyError, TypeError, ValueError):
            continue
        if not bbox.contains(p) or not 1 <= sit <= 5:
            continue
        try:
            delta = float(r["waterlevel_msl"]) - float(r["waterlevel_msl_previous"])
            rising = None if abs(delta) < 0.005 else delta > 0
        except (KeyError, TypeError, ValueError):
            rising = None
        province = (geo.get("province_name") or {}).get("th", "")
        out.append(
            RiverStation(
                code=str(st.get("tele_station_oldcode") or st.get("id")),
                name=(st.get("tele_station_name") or {}).get("th", ""),
                area=province,
                location=p,
                observed_at=t,
                bank_percent=pct,
                situation=sit,
                rising=rising,
            )
        )
    return out


async def fetch_levels(
    *, cfg: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> list[RiverStation]:
    cfg = cfg or settings()["river_level"]
    aoi = settings()["area_of_interest"]
    bbox = BoundingBox(**{k: aoi[k] for k in ("min_lat", "max_lat", "min_lon", "max_lon")})
    async with httpx.AsyncClient(timeout=cfg["timeout_s"], transport=transport) as client:
        resp = await request_with_retry(client, "GET", cfg["url"], max_retries=cfg["max_retries"])
    return parse_waterlevel(resp.json(), bbox)
