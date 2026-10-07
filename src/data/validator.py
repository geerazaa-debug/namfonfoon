"""Core geo types and data-integrity checks."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GeoPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)

    @field_validator("lat", "lon")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("coordinate must be finite")
        return v


class BoundingBox(BaseModel):
    min_lat: float = Field(ge=-90, le=90)
    max_lat: float = Field(ge=-90, le=90)
    min_lon: float = Field(ge=-180, le=180)
    max_lon: float = Field(ge=-180, le=180)

    def contains(self, p: GeoPoint) -> bool:
        return self.min_lat <= p.lat <= self.max_lat and self.min_lon <= p.lon <= self.max_lon

    def as_cmr(self) -> str:
        """CMR bounding_box order: lower-left lon,lat, upper-right lon,lat."""
        return f"{self.min_lon},{self.min_lat},{self.max_lon},{self.max_lat}"


class ValidationIssue(BaseModel):
    field: str
    message: str


def validate_timestamp(
    ts: datetime, *, max_age: timedelta, now: datetime | None = None, skew: timedelta = timedelta(minutes=5)
) -> list[ValidationIssue]:
    """Reject naive, future-dated, or stale timestamps."""
    if ts.tzinfo is None:
        return [ValidationIssue(field="timestamp", message="timestamp must be timezone-aware")]
    now = now or datetime.now(UTC)
    issues: list[ValidationIssue] = []
    if ts > now + skew:
        issues.append(ValidationIssue(field="timestamp", message="timestamp is in the future"))
    if now - ts > max_age:
        issues.append(ValidationIssue(field="timestamp", message=f"data older than {max_age}"))
    return issues


def validate_point_in_aoi(p: GeoPoint, aoi: BoundingBox) -> list[ValidationIssue]:
    if aoi.contains(p):
        return []
    return [ValidationIssue(field="location", message=f"({p.lat}, {p.lon}) outside area of interest")]


def validate_fraction(name: str, value: float) -> list[ValidationIssue]:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return [ValidationIssue(field=name, message="must be a finite value in [0, 1]")]
    return []
