"""PM2.5 near a point and along a route, from measured station readings (not a forecast)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from functools import lru_cache

from pydantic import BaseModel, Field

from src.config import settings
from src.data.air4thai import AirStation
from src.data.validator import GeoPoint
from src.engine.geofence import haversine_km
from src.engine.route_check import locate_on_route


class AirBand(BaseModel):
    id: int = Field(ge=1, le=5)
    emoji: str
    label: str
    color: str
    general: str  # advice for everyone
    sensitive: str  # advice for children, elderly, pregnant, chronically ill


class NearbyAir(BaseModel):
    station: AirStation
    distance_km: float


class AirOnRoute(BaseModel):
    worst: AirStation
    along_km: float
    stations: int  # fresh stations within the buffer


@lru_cache(maxsize=1)
def air_bands() -> dict[int, AirBand]:
    return {b["id"]: AirBand(**b) for b in settings()["air_quality"]["bands"]}


def _fresh(stations: Sequence[AirStation], now: datetime, max_age: timedelta) -> list[AirStation]:
    return [s for s in stations if now - max_age <= s.observed_at <= now + timedelta(minutes=5)]


def nearest_air(
    p: GeoPoint, stations: Sequence[AirStation], now: datetime, *, max_km: float, max_age: timedelta
) -> NearbyAir | None:
    best = min(
        ((haversine_km(p, s.location), s) for s in _fresh(stations, now, max_age)), default=None, key=lambda x: x[0]
    )
    if best is None or best[0] > max_km:
        return None
    return NearbyAir(station=best[1], distance_km=best[0])


def air_on_route(
    route: Sequence[GeoPoint], stations: Sequence[AirStation], now: datetime, *, buffer_km: float, max_age: timedelta
) -> AirOnRoute | None:
    near = []
    for s in _fresh(stations, now, max_age):
        dist, along = locate_on_route(s.location, route)
        if dist <= buffer_km:
            near.append((s, along))
    if not near:
        return None
    worst, along = max(near, key=lambda x: x[0].pm25)
    return AirOnRoute(worst=worst, along_km=along, stations=len(near))
