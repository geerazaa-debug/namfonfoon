"""Driving route between two points from an OSRM server (public demo by default: car profile, fair use).

Coordinates travel in the URL path, so callers must never log the URL or exception text.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.config import settings
from src.data.validator import GeoPoint
from src.retry import request_with_retry

# httpx logs every request URL at INFO; for OSRM that URL is a user's start and destination.
logging.getLogger("httpx").setLevel(logging.WARNING)


class RoutePath(BaseModel):
    points: list[GeoPoint] = Field(min_length=2)


class _Geometry(BaseModel):
    coordinates: list[tuple[float, float]]  # GeoJSON order: lon, lat


class _Route(BaseModel):
    geometry: _Geometry


class _Response(BaseModel):
    code: str
    routes: list[_Route] = Field(default_factory=list)


async def fetch_route(
    a: GeoPoint, b: GeoPoint, *, cfg: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> RoutePath | None:
    """None when OSRM finds no road route; raises httpx.HTTPError on transport/HTTP failure."""
    cfg = cfg or settings()["route_check"]
    url = f"{cfg['osrm_url']}/{a.lon:.5f},{a.lat:.5f};{b.lon:.5f},{b.lat:.5f}"
    async with httpx.AsyncClient(timeout=cfg["timeout_s"], transport=transport) as client:
        resp = await request_with_retry(
            client,
            "GET",
            url,
            params={"overview": "simplified", "geometries": "geojson"},
            max_retries=cfg["max_retries"],
        )
    body = _Response.model_validate(resp.json())
    if body.code != "Ok" or not body.routes or len(body.routes[0].geometry.coordinates) < 2:
        return None
    return RoutePath(points=[GeoPoint(lat=lat, lon=lon) for lon, lat in body.routes[0].geometry.coordinates])
