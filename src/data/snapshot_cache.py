"""One shared copy of an external snapshot (Air4Thai, BMA DDS) for all users, refreshed at most every `ttl`.

On a failed fetch the last good copy is returned (or None if there never was one). A lock keeps
concurrent requests at expiry from fetching twice.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import httpx
from pydantic import ValidationError

log = logging.getLogger(__name__)


class SnapshotCache[T]:
    def __init__(self, name: str, fetch: Callable[[], Awaitable[T]], ttl: Callable[[], timedelta]) -> None:
        self.name = name
        self.fetch = fetch  # looked up at call time by the caller's lambda, so tests can swap it
        self.ttl = ttl
        self._value: tuple[datetime, T] | None = None
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        self._value = None

    async def get(self, now: datetime) -> T | None:
        async with self._lock:
            if self._value and now - self._value[0] < self.ttl():
                return self._value[1]
            try:
                value = await self.fetch()
            except (httpx.HTTPError, ValidationError, ValueError) as exc:
                log.warning("%s fetch failed: %s", self.name, type(exc).__name__)
                return self._value[1] if self._value else None
            self._value = (now, value)
            return value
