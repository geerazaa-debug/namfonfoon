"""Rule-based rain advisory.

Not a predictive model: Step 4 showed no tested model beats rain-only, and rain-only is weak.
Levels come from rain thresholds in config/risk_thresholds.yaml (anchored on BMA DDS
recorded-flood rainfall), optionally raised one level in DDS flood-hotspot districts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.config import risk_thresholds
from src.data.validator import GeoPoint
from src.engine.districts import DistrictIndex, DistrictInfo, district_index


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(RiskLevel).index(self)


class RainObservation(BaseModel):
    """One nearby rain source (gauge or radar cell) covering the user's location."""

    source_id: str
    observed_at: datetime
    rain_1h_mm: float = Field(ge=0)
    rain_6h_mm: float | None = Field(default=None, ge=0)

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return v


class AlertInput(BaseModel):
    location: GeoPoint
    rain: list[RainObservation] = Field(default_factory=list, max_length=100)


class Assessment(BaseModel):
    location: GeoPoint
    district: DistrictInfo | None
    level: RiskLevel
    notify: bool
    stale: bool
    rain_1h_mm: float | None
    rain_6h_mm: float | None
    reasons: list[str]


def level_from(value: float | None, table: dict[str, float]) -> RiskLevel:
    if value is None:
        return RiskLevel.LOW
    for level in (RiskLevel.CRITICAL, RiskLevel.HIGH, RiskLevel.MODERATE):
        if value >= table[level.value]:
            return level
    return RiskLevel.LOW


class RuleBasedAnalyzer:
    def __init__(
        self,
        cfg: dict[str, Any] | None = None,
        districts: DistrictIndex | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        cfg = cfg or risk_thresholds()
        self.t1h: dict[str, float] = cfg["rain_1h_mm"]
        self.t6h: dict[str, float] = cfg["rain_6h_mm"]
        for name, t in (("rain_1h_mm", self.t1h), ("rain_6h_mm", self.t6h)):
            if not t["moderate"] < t["high"] < t["critical"]:
                raise ValueError(f"{name} thresholds must increase moderate < high < critical")
        self.escalate = bool(cfg["hotspot_escalation"])
        self.max_age = timedelta(minutes=cfg["max_obs_age_min"])
        self.min_notify = RiskLevel(cfg["alerts"]["min_level_to_notify"])
        self.districts = districts or district_index()
        self.now = now

    def assess(self, inp: AlertInput) -> Assessment:
        district = self.districts.locate(inp.location)
        fresh = [o for o in inp.rain if self.now() - o.observed_at <= self.max_age]
        if not fresh:
            return Assessment(
                location=inp.location, district=district, level=RiskLevel.LOW, notify=False, stale=True,
                rain_1h_mm=None, rain_6h_mm=None, reasons=["no fresh rain observation"],
            )  # fmt: skip

        r1 = max(o.rain_1h_mm for o in fresh)
        six = [o.rain_6h_mm for o in fresh if o.rain_6h_mm is not None]
        r6 = max(six) if six else None
        l1, l6 = level_from(r1, self.t1h), level_from(r6, self.t6h)
        level = max(l1, l6, key=lambda lv: lv.rank)
        reasons = [f"rain_1h {r1:.0f} mm -> {l1}"] + ([f"rain_6h {r6:.0f} mm -> {l6}"] if r6 is not None else [])

        if self.escalate and district and district.hotspot and RiskLevel.MODERATE.rank <= level.rank:
            if level is not RiskLevel.CRITICAL:
                level = list(RiskLevel)[level.rank + 1]
                reasons.append(
                    f"DDS flood hotspot district ({district.flood_days_2022_2025} flood days 2022-25) -> {level}"
                )

        return Assessment(
            location=inp.location, district=district, level=level, notify=level.rank >= self.min_notify.rank,
            stale=False, rain_1h_mm=r1, rain_6h_mm=r6, reasons=reasons,
        )  # fmt: skip
