"""BMA Department of Drainage and Sewerage (สำนักการระบายน้ำ) live data from weather.bangkok.go.th.

These are the JSON calls the public map page itself makes, not a documented API: no stated licence,
shape may change without notice. Used for display (route checks, /map) only, with attribution, a short
shared cache and an identifying User-Agent; permission requested in docs/bma_data_request_draft.md.
Rain gauges: ~127, 5-min updates. Road flood-depth sensors: ~247 (cm).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from src.config import settings
from src.data.validator import GeoPoint
from src.engine.route_check import RoadFlood, StationRain
from src.retry import request_with_retry

BKK_TZ = timezone(timedelta(hours=7))
_MS = re.compile(r"/Date\((\d+)\)/")


def _thai_time(s: str) -> datetime:
    """ "16/10/2569 03:05" (Buddhist year, Bangkok time) -> aware UTC datetime."""
    d = datetime.strptime(s, "%d/%m/%Y %H:%M")
    return d.replace(year=d.year - 543, tzinfo=BKK_TZ).astimezone(UTC)


def parse_rain(payload: list[dict[str, Any]]) -> list[StationRain]:
    out = []
    for r in payload:
        m = _MS.fullmatch(str(r.get("site_timestamp", "")))
        try:
            lat, lon, mm = float(r["latitude"]), float(r["longitude"]), float(r["rf1hr"])
        except (KeyError, TypeError, ValueError):
            continue
        if not m or r.get("status") != 1 or mm < 0:
            continue
        out.append(
            StationRain(
                station_id=f"bma:{r.get('rain_code') or r.get('rain_id')}",
                location=GeoPoint(lat=lat, lon=lon),
                district=r.get("district_name") or None,
                observed_at=datetime.fromtimestamp(int(m.group(1)) / 1000, UTC),
                rain_1h_mm=mm,
                rain_6h_mm=r["rf6hr"] if isinstance(r.get("rf6hr"), int | float) and r["rf6hr"] >= 0 else None,
            )
        )
    return out


def parse_floods(payload: dict[str, Any]) -> list[RoadFlood]:
    """Road sensors only (typesite 1), joined with the status table; faulty sensors are dropped."""
    status = {r.get("flood_id"): r for r in payload.get("dtTbl", [])}
    out = []
    for r in payload.get("floodTbl", []):
        st = status.get(r.get("flood_id")) or {}
        if r.get("typesite") != 1 or st.get("status") != 1:
            continue
        try:
            lat, lon, depth = float(r["latitude"]), float(r["longitude"]), float(r.get("flood") or 0)
            t = _thai_time(r["site_timestatmpTH"])
        except (KeyError, TypeError, ValueError):
            continue
        if depth < 0:
            continue
        out.append(
            RoadFlood(
                sensor_id=str(r.get("flood_code") or r.get("flood_id")),
                name=r.get("flood_short_name") or st.get("flood_shortname") or "",
                district=st.get("districtName") or None,
                location=GeoPoint(lat=lat, lon=lon),
                observed_at=t,
                depth_cm=depth,
            )
        )
    return out


def _client(cfg: dict[str, Any], transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    headers = {"User-Agent": cfg["user_agent"], "X-Requested-With": "XMLHttpRequest"}
    return httpx.AsyncClient(timeout=cfg["timeout_s"], transport=transport, headers=headers)


async def fetch_rain(
    *, cfg: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> list[StationRain]:
    cfg = cfg or settings()["bma_dds"]
    async with _client(cfg, transport) as client:
        resp = await request_with_retry(client, "POST", cfg["rain_url"], max_retries=cfg["max_retries"])
    return parse_rain(resp.json())


async def fetch_floods(
    *, cfg: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> list[RoadFlood]:
    cfg = cfg or settings()["bma_dds"]
    async with _client(cfg, transport) as client:
        resp = await request_with_retry(client, "GET", cfg["flood_url"], max_retries=cfg["max_retries"])
    return parse_floods(resp.json())
