"""Flood risk scoring.

Transparent weighted-factor model, not a trained ML model. Weights in
config/risk_thresholds.yaml are uncalibrated until backtested against
historical flood events.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.config import risk_thresholds
from src.data.validator import GeoPoint


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(RiskLevel).index(self)


class WaterLevelObs(BaseModel):
    timestamp: datetime
    level_m: float


class RiskInput(BaseModel):
    location: GeoPoint
    satellite_water_fraction: float = Field(ge=0, le=1)
    low_lying_factor: float = Field(ge=0, le=1)
    rainfall_mm_24h: float = Field(ge=0)
    water_levels: list[WaterLevelObs] = []
    warning_level_m: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _levels_need_warning(self) -> RiskInput:
        if self.water_levels and self.warning_level_m is None:
            raise ValueError("warning_level_m is required when water_levels are given")
        return self


class RiskAssessment(BaseModel):
    location: GeoPoint
    score: float = Field(ge=0, le=100)
    level: RiskLevel
    factors: dict[str, float]
    rise_rate_m_per_h: float | None = None
    projected_level_m: float | None = None


def rise_rate_m_per_h(obs: list[WaterLevelObs]) -> float | None:
    """Least-squares slope of level vs time. None if < 2 points or zero time span."""
    if len(obs) < 2:
        return None
    ts = [o.timestamp.timestamp() / 3600 for o in obs]
    ys = [o.level_m for o in obs]
    tm, ym = sum(ts) / len(ts), sum(ys) / len(ys)
    var = sum((t - tm) ** 2 for t in ts)
    if var == 0:
        return None
    return sum((t - tm) * (y - ym) for t, y in zip(ts, ys, strict=False)) / var


class RiskAnalyzer:
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or risk_thresholds()
        self.weights: dict[str, float] = cfg["weights"]
        self.norm: dict[str, float] = cfg["normalization"]
        self.levels = sorted(
            ((RiskLevel(name), float(lo)) for name, lo in cfg["levels"].items()), key=lambda x: x[1], reverse=True
        )
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"risk weights must sum to 1.0, got {total}")

    def level_for(self, score: float) -> RiskLevel:
        for level, lo in self.levels:
            if score >= lo:
                return level
        return RiskLevel.LOW

    def assess(self, inp: RiskInput) -> RiskAssessment:
        rain = min(1.0, inp.rainfall_mm_24h / self.norm["rainfall_mm_24h_max"])

        rate = projected = None
        trend = 0.0
        if inp.water_levels and inp.warning_level_m is not None:
            latest = max(inp.water_levels, key=lambda o: o.timestamp)
            rate = rise_rate_m_per_h(inp.water_levels)
            projected = latest.level_m + max(0.0, rate or 0.0) * self.norm["projection_horizon_h"]
            trend = max(0.0, min(1.0, projected / inp.warning_level_m))

        factors = {
            "satellite_water": inp.satellite_water_fraction,
            "low_elevation": inp.low_lying_factor,
            "rainfall": rain,
            "water_level_trend": trend,
        }
        score = round(100 * sum(self.weights[k] * v for k, v in factors.items()), 2)
        return RiskAssessment(
            location=inp.location,
            score=score,
            level=self.level_for(score),
            factors=factors,
            rise_rate_m_per_h=rate,
            projected_level_m=projected,
        )
