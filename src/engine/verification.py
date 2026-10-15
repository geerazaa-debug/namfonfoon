"""Per-district verification snapshot for the step 5 pre-registration (docs/step5_alert_verification_prereg.md).

Every poll, each district is evaluated at its representative point with the unchanged live rules, once from
ThaiWater gauges (what users get) and once from BMA DDS gauges, next to what BMA road sensors measure there.
No users or subscriber locations are involved.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel

from src.data.validator import GeoPoint
from src.engine.districts import DistrictInfo
from src.engine.geofence import haversine_km
from src.engine.risk_analyzer import AlertInput, RainObservation, RuleBasedAnalyzer
from src.engine.route_check import RoadFlood, StationRain


class VerificationRow(BaseModel):
    observed_at: datetime
    district: str
    tw_level: str
    tw_rain_1h: float | None
    tw_rain_6h: float | None
    bma_level: str
    bma_rain_1h: float | None
    bma_rain_6h: float | None
    sensors_online: int
    max_depth_cm: float | None  # None when no sensor online


def near_observations(
    p: GeoPoint, stations: Sequence[StationRain], radius_km: float, fallback_km: float
) -> list[RainObservation]:
    """Same choice as RainAccumulator.near: every gauge within radius, else the nearest within fallback."""
    dist = sorted(((haversine_km(p, s.location), s) for s in stations), key=lambda x: x[0])
    chosen = [s for d, s in dist if d <= radius_km]
    if not chosen and dist and dist[0][0] <= fallback_km:
        chosen = [dist[0][1]]
    return [
        RainObservation(
            source_id=s.station_id, observed_at=s.observed_at, rain_1h_mm=s.rain_1h_mm, rain_6h_mm=s.rain_6h_mm
        )
        for s in chosen
    ]


def snapshot(
    now: datetime,
    points: dict[str, GeoPoint],
    analyzer: RuleBasedAnalyzer,
    thaiwater: Callable[[GeoPoint], list[RainObservation]],
    bma_gauges: Sequence[StationRain],
    floods: Sequence[RoadFlood],
    district_of: Callable[[GeoPoint], DistrictInfo | None],
    *,
    radius_km: float,
    fallback_km: float,
) -> list[VerificationRow]:
    depths: dict[str, list[float]] = {}
    for f in floods:  # locate by polygon, not BMA's district text, so names match `points`
        d = district_of(f.location)
        if d:
            depths.setdefault(d.th, []).append(f.depth_cm)
    rows = []
    for name, p in points.items():
        tw = analyzer.assess(AlertInput(location=p, rain=thaiwater(p)))
        bma = analyzer.assess(AlertInput(location=p, rain=near_observations(p, bma_gauges, radius_km, fallback_km)))
        here = depths.get(name, [])
        rows.append(
            VerificationRow(
                observed_at=now,
                district=name,
                tw_level=tw.level.value,
                tw_rain_1h=tw.rain_1h_mm,
                tw_rain_6h=tw.rain_6h_mm,
                bma_level=bma.level.value,
                bma_rain_1h=bma.rain_1h_mm,
                bma_rain_6h=bma.rain_6h_mm,
                sensors_online=len(here),
                max_depth_cm=max(here) if here else None,
            )
        )
    return rows
