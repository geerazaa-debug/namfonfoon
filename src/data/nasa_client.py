"""Async NASA client: EONET flood events + CMR flood-product granule metadata.

Both endpoints are public for search. NASA_API_KEY is attached only to api.nasa.gov
hosts; EARTHDATA_TOKEN is only needed to download granule files (not done here).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Self

import httpx
from pydantic import BaseModel

from src.config import settings
from src.data.validator import BoundingBox, GeoPoint
from src.retry import request_with_retry


class FloodEvent(BaseModel):
    id: str
    title: str
    date: datetime
    location: GeoPoint
    closed: datetime | None = None
    source_urls: list[str] = []


class FloodGranule(BaseModel):
    id: str
    title: str
    time_start: datetime | None
    time_end: datetime | None
    links: list[str] = []


class NasaClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        cfg: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cfg = cfg or settings()["nasa"]
        self.api_key = api_key if api_key is not None else os.environ.get("NASA_API_KEY", "")
        self._client = httpx.AsyncClient(timeout=self.cfg["timeout_s"], transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        if self.api_key and httpx.URL(url).host == "api.nasa.gov":
            params = {**params, "api_key": self.api_key}
        resp = await request_with_retry(
            self._client,
            "GET",
            url,
            params=params,
            max_retries=self.cfg["max_retries"],
            backoff_base_s=self.cfg["backoff_base_s"],
        )
        return resp.json()

    async def flood_events(self, bbox: BoundingBox, *, days: int = 30, status: str = "all") -> list[FloodEvent]:
        """EONET flood events intersecting bbox. Uses the latest point geometry per event."""
        data = await self._get_json(
            self.cfg["eonet_url"],
            {
                "category": "floods",
                "days": days,
                "status": status,
                "bbox": f"{bbox.min_lon},{bbox.max_lat},{bbox.max_lon},{bbox.min_lat}",
            },
        )
        events: list[FloodEvent] = []
        for ev in data.get("events", []):
            points = [g for g in ev.get("geometry", []) if g.get("type") == "Point"]
            if not points:
                continue  # polygon-only events skipped for now
            g = max(points, key=lambda g: g["date"])
            lon, lat = g["coordinates"][:2]
            events.append(
                FloodEvent(
                    id=ev["id"],
                    title=ev.get("title", ""),
                    date=g["date"],
                    location=GeoPoint(lat=lat, lon=lon),
                    closed=ev.get("closed"),
                    source_urls=[s["url"] for s in ev.get("sources", []) if "url" in s],
                )
            )
        return events

    async def flood_granules(
        self, bbox: BoundingBox, start: datetime, end: datetime, *, page_size: int = 50
    ) -> list[FloodGranule]:
        """CMR granule metadata for the configured flood product within bbox and time range."""
        data = await self._get_json(
            self.cfg["cmr_granules_url"],
            {
                "short_name": self.cfg["flood_collection_short_name"],
                "bounding_box": bbox.as_cmr(),
                "temporal": f"{start.isoformat()},{end.isoformat()}",
                "page_size": page_size,
                "sort_key": "-start_date",
            },
        )
        return [
            FloodGranule(
                id=e["id"],
                title=e.get("title", ""),
                time_start=e.get("time_start"),
                time_end=e.get("time_end"),
                links=[lk["href"] for lk in e.get("links", []) if "href" in lk],
            )
            for e in data.get("feed", {}).get("entry", [])
        ]
