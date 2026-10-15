"""Latest PM2.5 per station from Air4Thai (Pollution Control Department; includes BMA's own sensors).

One nationwide JSON (~130 KB, hourly). Missing readings are "-1"; the band is Air4Thai's own PM25
`color_id` (Thai AQI), so the thresholds stay PCD's, not ours.
"""

from __future__ import annotations

import ssl
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import certifi
import httpx
from pydantic import BaseModel, Field

from src.config import CONFIG_DIR, settings
from src.data.validator import GeoPoint
from src.retry import request_with_retry

BKK_TZ = timezone(timedelta(hours=7))


class AirStation(BaseModel):
    station_id: str
    name: str
    area: str
    location: GeoPoint
    observed_at: datetime
    pm25: float = Field(ge=0)  # ug/m3
    band: int = Field(ge=1, le=5)


def parse_air4thai(payload: dict[str, Any]) -> list[AirStation]:
    out = []
    for s in payload.get("stations", []):
        last = s.get("AQILast") or {}
        pm = last.get("PM25") or {}
        try:
            value, band = float(pm.get("value")), int(pm.get("color_id"))
            lat, lon = float(s["lat"]), float(s["long"])
            t = datetime.strptime(f"{last['date']} {last['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=BKK_TZ)
        except (TypeError, ValueError, KeyError):
            continue
        if value < 0 or not 1 <= band <= 5:
            continue
        out.append(
            AirStation(
                station_id=str(s.get("stationID")),
                name=s.get("nameTH") or "",
                area=s.get("areaTH") or "",
                location=GeoPoint(lat=lat, lon=lon),
                observed_at=t,
                pm25=value,
                band=band,
            )
        )
    return out


@lru_cache(maxsize=4)
def tls_context(extra_ca: str) -> ssl.SSLContext:
    """certifi + the intermediates Air4Thai's server fails to send (see config/ca/letsencrypt_yr.pem)."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(CONFIG_DIR / extra_ca))
    return ctx


async def fetch_air(
    *, cfg: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> list[AirStation]:
    cfg = cfg or settings()["air_quality"]
    verify = tls_context(cfg["extra_ca"]) if cfg.get("extra_ca") else True
    async with httpx.AsyncClient(timeout=cfg["timeout_s"], transport=transport, verify=verify) as client:
        resp = await request_with_retry(client, "GET", cfg["url"], max_retries=cfg["max_retries"])
    return parse_air4thai(resp.json())
