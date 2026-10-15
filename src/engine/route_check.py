"""Route check: measured rain at gauges near a route + live citizen water reports on it.

Not a forecast: gauges report the last hour's rain, reports are unverified observations.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field

from src.config import settings
from src.data.validator import GeoPoint
from src.engine.community import MapReport, ReportLevel
from src.engine.districts import DistrictInfo
from src.engine.geofence import haversine_km, segment_closest


class RainClass(BaseModel):
    key: str
    min_mm: float = Field(ge=0)  # 1 h rain at a gauge
    emoji: str
    label: str


class StationRain(BaseModel):
    station_id: str
    location: GeoPoint
    district: str | None  # None outside Bangkok
    observed_at: datetime
    rain_1h_mm: float = Field(ge=0)
    rain_6h_mm: float | None = Field(default=None, ge=0)  # BMA reports it; ThaiWater rows leave it None


class RoadFlood(BaseModel):
    """A BMA road flood-depth sensor reading."""

    sensor_id: str
    name: str
    district: str | None
    location: GeoPoint
    observed_at: datetime
    depth_cm: float = Field(ge=0)


class FloodOnRoute(BaseModel):
    along_km: float
    sensor: RoadFlood


class RainStretch(BaseModel):
    """Wettest gauge per district along the route."""

    district: str | None
    along_km: float
    rain_1h_mm: float
    rain: RainClass


class ReportOnRoute(BaseModel):
    along_km: float
    report: MapReport


Verdict = Literal["clear", "caution", "warn", "no_data"]


class RouteCheck(BaseModel):
    distance_km: float
    verdict: Verdict
    stations_checked: int  # fresh gauges near the route, wet or dry
    rain: list[RainStretch]  # route order
    reports: list[ReportOnRoute]  # route order, flood levels only
    flood_sensors_checked: int  # fresh BMA road sensors near the route, wet or dry
    road_floods: list[FloodOnRoute]  # route order, depth >= min_flood_cm
    hotspots: list[str]  # districts on the route with frequent recorded floods, route order


@lru_cache(maxsize=1)
def rain_classes() -> list[RainClass]:
    return sorted((RainClass(**c) for c in settings()["route_check"]["rain_classes"]), key=lambda c: c.min_mm)


def rain_class(mm: float, classes: Sequence[RainClass]) -> RainClass | None:
    hit = [c for c in classes if mm >= c.min_mm]
    return max(hit, key=lambda c: c.min_mm) if hit else None


def route_length_km(route: Sequence[GeoPoint]) -> float:
    return sum(haversine_km(a, b) for a, b in zip(route, route[1:], strict=False))


def locate_on_route(p: GeoPoint, route: Sequence[GeoPoint]) -> tuple[float, float]:
    """(km from p to the route, km along the route to the closest point)."""
    if len(route) == 1:
        return haversine_km(p, route[0]), 0.0
    best, along, start = float("inf"), 0.0, 0.0
    for a, b in zip(route, route[1:], strict=False):
        seg = haversine_km(a, b)
        d, t = segment_closest(p, a, b)
        if d < best:
            best, along = d, start + t * seg
        start += seg
    return best, along


def sample_route(route: Sequence[GeoPoint], step_km: float) -> list[GeoPoint]:
    """Route vertices plus points every step_km, so long straight segments don't skip a district."""
    out = [route[0]]
    for a, b in zip(route, route[1:], strict=False):
        n = int(haversine_km(a, b) // step_km)
        out += [
            GeoPoint(lat=a.lat + (b.lat - a.lat) * i / (n + 1), lon=a.lon + (b.lon - a.lon) * i / (n + 1))
            for i in range(1, n + 1)
        ]
        out.append(b)
    return out


def check_route(
    route: Sequence[GeoPoint],
    stations: Sequence[StationRain],
    reports: Sequence[MapReport],
    now: datetime,
    *,
    station_buffer_km: float,
    report_buffer_m: float,
    max_rain_age: timedelta,
    warn_rain_mm: float,
    warn_report_rank: int,
    levels: dict[str, ReportLevel],
    district_of: Callable[[GeoPoint], DistrictInfo | None],
    classes: Sequence[RainClass] | None = None,
    road_floods: Sequence[RoadFlood] = (),
    flood_buffer_m: float = 200,
    min_flood_cm: float = 2,
    warn_flood_cm: float = 10,
    max_flood_age: timedelta = timedelta(minutes=30),
) -> RouteCheck:
    classes = classes or rain_classes()
    pad = station_buffer_km / 100  # ~1 km per 0.01 deg; cheap prefilter before the per-segment scan
    lats, lons = [p.lat for p in route], [p.lon for p in route]

    checked = 0
    wettest: dict[str | None, RainStretch] = {}
    for s in stations:
        if not (now - max_rain_age <= s.observed_at <= now):
            continue
        if not (
            min(lats) - pad <= s.location.lat <= max(lats) + pad
            and min(lons) - pad <= s.location.lon <= max(lons) + pad
        ):
            continue
        dist, along = locate_on_route(s.location, route)
        if dist > station_buffer_km:
            continue
        checked += 1
        cls = rain_class(s.rain_1h_mm, classes)
        prev = wettest.get(s.district)
        if cls and (prev is None or s.rain_1h_mm > prev.rain_1h_mm):
            wettest[s.district] = RainStretch(district=s.district, along_km=along, rain_1h_mm=s.rain_1h_mm, rain=cls)

    on_route = []
    for r in reports:
        lv = levels.get(r.level)
        if lv is None or lv.rank == 0:  # "water gone" is good news, not a hazard on the route
            continue
        dist, along = locate_on_route(GeoPoint(lat=r.lat, lon=r.lon), route)
        if dist * 1000 <= report_buffer_m:
            on_route.append(ReportOnRoute(along_km=along, report=r))

    sensors_checked, flooded = 0, []
    for f in road_floods:
        if not (now - max_flood_age <= f.observed_at <= now + timedelta(minutes=5)):
            continue
        dist, along = locate_on_route(f.location, route)
        if dist * 1000 > flood_buffer_m:
            continue
        sensors_checked += 1
        if f.depth_cm >= min_flood_cm:
            flooded.append(FloodOnRoute(along_km=along, sensor=f))

    hotspots: list[str] = []
    for p in sample_route(route, 0.5):
        d = district_of(p)
        if d and d.hotspot and d.th not in hotspots:
            hotspots.append(d.th)

    rain = sorted(wettest.values(), key=lambda x: x.along_km)
    worst_rank = max((levels[r.report.level].rank for r in on_route), default=0)
    deep = any(f.sensor.depth_cm >= warn_flood_cm for f in flooded)
    if any(x.rain_1h_mm >= warn_rain_mm for x in rain) or worst_rank >= warn_report_rank or deep:
        verdict: Verdict = "warn"
    elif rain or on_route or flooded:
        verdict = "caution"
    elif checked == 0 and sensors_checked == 0:
        verdict = "no_data"
    else:
        verdict = "clear"
    return RouteCheck(
        distance_km=route_length_km(route),
        verdict=verdict,
        stations_checked=checked,
        rain=rain,
        reports=sorted(on_route, key=lambda x: x.along_km),
        flood_sensors_checked=sensors_checked,
        road_floods=sorted(flooded, key=lambda x: x.along_km),
        hotspots=hotspots,
    )
