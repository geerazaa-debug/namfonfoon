"""Async client for a local water-level sensor feed.

The feed schema is provider-specific; this client expects
{"readings": [{"station_id", "lat", "lon", "water_level_m", "warning_level_m", "timestamp"}]}.
Adapt `_parse` when wiring a real provider (e.g. ThaiWater, BMA flood sensors).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Self

import httpx
from pydantic import BaseModel, Field

from src.config import settings
from src.data.validator import GeoPoint
from src.retry import request_with_retry


class SensorReading(BaseModel):
    station_id: str
    location: GeoPoint
    water_level_m: float
    warning_level_m: float = Field(gt=0)
    timestamp: datetime


class LocalSensorClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        cfg: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cfg = cfg or settings()["local_sensors"]
        self.base_url = base_url or os.environ.get("LOCAL_SENSOR_BASE_URL", "")
        key = api_key if api_key is not None else os.environ.get("LOCAL_SENSOR_API_KEY", "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.AsyncClient(timeout=self.cfg["timeout_s"], headers=headers, transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def latest_readings(self) -> list[SensorReading]:
        if not self.base_url:
            raise RuntimeError("LOCAL_SENSOR_BASE_URL is not configured")
        resp = await request_with_retry(
            self._client, "GET", f"{self.base_url.rstrip('/')}/readings", max_retries=self.cfg["max_retries"]
        )
        return self._parse(resp.json())

    @staticmethod
    def _parse(payload: dict[str, Any]) -> list[SensorReading]:
        return [
            SensorReading(
                station_id=r["station_id"],
                location=GeoPoint(lat=r["lat"], lon=r["lon"]),
                water_level_m=r["water_level_m"],
                warning_level_m=r["warning_level_m"],
                timestamp=r["timestamp"],
            )
            for r in payload.get("readings", [])
        ]
