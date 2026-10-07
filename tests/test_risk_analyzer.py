from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.data.validator import GeoPoint
from src.engine.elevation_mapper import ElevationMapper, ElevationSample
from src.engine.risk_analyzer import RiskAnalyzer, RiskInput, RiskLevel, WaterLevelObs, rise_rate_m_per_h

P = GeoPoint(lat=13.75, lon=100.5)
T0 = datetime(2026, 10, 7, tzinfo=UTC)


def obs(levels: list[float]) -> list[WaterLevelObs]:
    return [WaterLevelObs(timestamp=T0 + timedelta(hours=i), level_m=v) for i, v in enumerate(levels)]


def test_zero_inputs_is_low():
    a = RiskAnalyzer().assess(RiskInput(location=P, satellite_water_fraction=0, low_lying_factor=0, rainfall_mm_24h=0))
    assert a.score == 0 and a.level is RiskLevel.LOW


def test_max_inputs_is_critical():
    a = RiskAnalyzer().assess(
        RiskInput(
            location=P,
            satellite_water_fraction=1,
            low_lying_factor=1,
            rainfall_mm_24h=500,
            water_levels=obs([1.0, 1.5, 2.0]),
            warning_level_m=2.0,
        )
    )
    assert a.score == 100 and a.level is RiskLevel.CRITICAL


def test_rise_rate_and_projection():
    assert rise_rate_m_per_h(obs([1.0, 1.2, 1.4])) == pytest.approx(0.2)
    a = RiskAnalyzer().assess(
        RiskInput(
            location=P,
            satellite_water_fraction=0,
            low_lying_factor=0,
            rainfall_mm_24h=0,
            water_levels=obs([1.0, 1.2, 1.4]),
            warning_level_m=4.0,
        )
    )
    assert a.projected_level_m == pytest.approx(1.4 + 0.2 * 3)
    assert a.factors["water_level_trend"] == pytest.approx(0.5)


def test_falling_water_does_not_project_lower():
    a = RiskAnalyzer().assess(
        RiskInput(
            location=P,
            satellite_water_fraction=0,
            low_lying_factor=0,
            rainfall_mm_24h=0,
            water_levels=obs([2.0, 1.8]),
            warning_level_m=4.0,
        )
    )
    assert a.projected_level_m == pytest.approx(1.8)


def test_level_boundaries():
    r = RiskAnalyzer()
    assert r.level_for(29.99) is RiskLevel.LOW
    assert r.level_for(30) is RiskLevel.MODERATE
    assert r.level_for(55) is RiskLevel.HIGH
    assert r.level_for(75) is RiskLevel.CRITICAL


def test_rejects_bad_weights():
    cfg = {
        "weights": {"satellite_water": 0.5, "low_elevation": 0.1, "rainfall": 0.1, "water_level_trend": 0.1},
        "normalization": {},
        "levels": {"low": 0},
    }
    with pytest.raises(ValueError, match="sum to 1.0"):
        RiskAnalyzer(cfg)


def test_input_validation():
    with pytest.raises(ValidationError):
        RiskInput(location=P, satellite_water_fraction=1.5, low_lying_factor=0, rainfall_mm_24h=0)
    with pytest.raises(ValidationError):
        RiskInput(
            location=P, satellite_water_fraction=0, low_lying_factor=0, rainfall_mm_24h=0, water_levels=obs([1.0])
        )  # missing warning_level_m
    with pytest.raises(ValidationError):
        GeoPoint(lat=float("nan"), lon=100)


def test_elevation_low_lying_factor():
    samples = [
        ElevationSample(location=GeoPoint(lat=13.75, lon=100.50), elevation_m=0.0),
        ElevationSample(location=GeoPoint(lat=13.76, lon=100.50), elevation_m=3.0),
        ElevationSample(location=GeoPoint(lat=13.74, lon=100.50), elevation_m=3.0),
        ElevationSample(location=GeoPoint(lat=13.75, lon=100.51), elevation_m=3.0),
    ]
    m = ElevationMapper(samples)
    assert m.elevation_at(P) == 0.0
    # local mean 2.25 m, point at 0 -> 2.25 / 3.0
    assert m.low_lying_factor(P, radius_km=2.0, scale_m=3.0) == pytest.approx(0.75)
