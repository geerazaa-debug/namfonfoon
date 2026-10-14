"""Spatial geofencing: point and route proximity to circular flood zones."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from src.data.validator import GeoPoint

EARTH_RADIUS_KM = 6371.0088


class FloodZone(BaseModel):
    zone_id: str
    center: GeoPoint
    radius_km: float = Field(gt=0)
    risk_score: float = Field(ge=0, le=100)
    level: str


class ZoneHit(BaseModel):
    zone: FloodZone
    distance_km: float  # distance from the zone edge; 0 means inside


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def segment_closest(p: GeoPoint, a: GeoPoint, b: GeoPoint) -> tuple[float, float]:
    """(km from p to segment ab, fraction 0..1 along ab of the closest point).

    Local equirectangular projection: fine for < ~100 km.
    """
    k = math.cos(math.radians(p.lat))
    ax, ay = (a.lon - p.lon) * k, a.lat - p.lat
    bx, by = (b.lon - p.lon) * k, b.lat - p.lat
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.radians(math.hypot(cx, cy)) * EARTH_RADIUS_KM, t


def point_segment_km(p: GeoPoint, a: GeoPoint, b: GeoPoint) -> float:
    return segment_closest(p, a, b)[0]


def zones_near_point(p: GeoPoint, zones: Iterable[FloodZone], buffer_km: float = 0.0) -> list[ZoneHit]:
    """Zones whose edge is within buffer_km of p, nearest first."""
    hits = []
    for z in zones:
        edge = max(0.0, haversine_km(p, z.center) - z.radius_km)
        if edge <= buffer_km:
            hits.append(ZoneHit(zone=z, distance_km=edge))
    return sorted(hits, key=lambda h: h.distance_km)


def zones_on_route(route: Sequence[GeoPoint], zones: Iterable[FloodZone], buffer_km: float = 0.0) -> list[ZoneHit]:
    """Zones whose edge comes within buffer_km of any segment of the route polyline."""
    if not route:
        return []
    if len(route) == 1:
        return zones_near_point(route[0], zones, buffer_km)
    hits = []
    for z in zones:
        d = min(point_segment_km(z.center, a, b) for a, b in zip(route, route[1:], strict=False))
        edge = max(0.0, d - z.radius_km)
        if edge <= buffer_km:
            hits.append(ZoneHit(zone=z, distance_km=edge))
    return sorted(hits, key=lambda h: h.distance_km)
