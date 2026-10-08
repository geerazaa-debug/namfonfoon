"""Poll rain -> assess each subscriber -> push LINE advisory, with cooldown deduplication.

Cooldown key = (user, district or ~1 km cell). A key that was alerted is silent for
`alerts.cooldown_min` unless the new level is higher than the last one sent.
A failed push is not recorded, so it is retried on the next poll.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from src.alerts.payload_builder import build_rain_alert
from src.config import risk_thresholds, settings
from src.data.thaiwater_rain import RainAccumulator, ThaiWaterRainClient
from src.data.validator import BoundingBox, GeoPoint
from src.engine.risk_analyzer import AlertInput, Assessment, RiskLevel, RuleBasedAnalyzer

log = logging.getLogger(__name__)

Sender = Callable[[str, list[dict[str, Any]]], Awaitable[object]]


class CooldownTracker:
    def __init__(self, cooldown: timedelta) -> None:
        self.cooldown = cooldown
        self._last: dict[tuple[str, str], tuple[datetime, RiskLevel]] = {}

    def should_send(self, key: tuple[str, str], level: RiskLevel, now: datetime) -> bool:
        prev = self._last.get(key)
        if prev is None:
            return True
        sent_at, sent_level = prev
        return now - sent_at >= self.cooldown or level.rank > sent_level.rank

    def record(self, key: tuple[str, str], level: RiskLevel, now: datetime) -> None:
        self._last[key] = (now, level)

    def forget_user(self, user_id: str) -> None:
        self._last = {k: v for k, v in self._last.items() if k[0] != user_id}


class RunReport(BaseModel):
    stations: int
    subscribers: int
    notify: int
    sent: int
    suppressed_cooldown: int
    failed: int
    no_data: int


def cooldown_key(user_id: str, a: Assessment) -> tuple[str, str]:
    area = a.district.th if a.district else f"{round(a.location.lat, 2)},{round(a.location.lon, 2)}"
    return (user_id, area)


class AlertPipeline:
    def __init__(
        self,
        *,
        fetch: Callable[[BoundingBox], Awaitable[list]],
        subscribers: Mapping[str, GeoPoint],
        send: Sender | None,
        analyzer: RuleBasedAnalyzer | None = None,
        accumulator: RainAccumulator | None = None,
        cooldown: CooldownTracker | None = None,
        bbox: BoundingBox | None = None,
    ) -> None:
        self.fetch = fetch
        self.subscribers = subscribers
        self.send = send  # None = dry run: assess and count, never push
        self.analyzer = analyzer or RuleBasedAnalyzer()
        self.acc = accumulator or RainAccumulator()
        self.cooldown = cooldown or CooldownTracker(timedelta(minutes=risk_thresholds()["alerts"]["cooldown_min"]))
        aoi = settings()["area_of_interest"]
        self.bbox = bbox or BoundingBox(**{k: aoi[k] for k in ("min_lat", "max_lat", "min_lon", "max_lon")})

    async def run_once(self, now: datetime | None = None) -> RunReport:
        now = now or datetime.now(UTC)
        snaps = await self.fetch(self.bbox)
        self.acc.add(snaps)
        counts = dict(notify=0, sent=0, suppressed_cooldown=0, failed=0, no_data=0)
        for user_id, loc in list(self.subscribers.items()):
            a = self.analyzer.assess(AlertInput(location=loc, rain=self.acc.near(loc)))
            if a.stale:
                counts["no_data"] += 1
            if not a.notify:
                continue
            counts["notify"] += 1
            key = cooldown_key(user_id, a)
            if not self.cooldown.should_send(key, a.level, now):
                counts["suppressed_cooldown"] += 1
                continue
            if self.send is None:
                continue
            try:
                await self.send(user_id, [build_rain_alert(a)])
            except Exception:  # one failed push must not stop the others; retried next poll
                counts["failed"] += 1
                log.exception("LINE push failed")  # no user id / location in logs
                continue
            self.cooldown.record(key, a.level, now)
            counts["sent"] += 1
        report = RunReport(stations=len(snaps), subscribers=len(self.subscribers), **counts)
        log.info("rain poll: %s", report.model_dump())
        return report

    async def run_forever(self, interval_s: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                log.exception("rain poll failed; will retry next interval")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass


def default_fetch() -> Callable[[BoundingBox], Awaitable[list]]:
    async def fetch(bbox: BoundingBox) -> list:
        async with ThaiWaterRainClient() as c:
            return await c.latest(bbox)

    return fetch
