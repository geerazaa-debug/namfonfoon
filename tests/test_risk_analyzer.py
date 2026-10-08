import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shapely.geometry import shape

from src.alerts.payload_builder import build_rain_alert
from src.api import server
from src.config import CONFIG_DIR, risk_thresholds
from src.data.validator import GeoPoint
from src.engine.districts import DistrictIndex, district_index
from src.engine.risk_analyzer import AlertInput, RainObservation, RiskLevel, RuleBasedAnalyzer, level_from

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def inside(th: str) -> GeoPoint:
    gj = json.loads((CONFIG_DIR / "bangkok_districts.geojson").read_text(encoding="utf-8"))
    f = next(f for f in gj["features"] if f["properties"]["th"] == th)
    p = shape(f["geometry"]).representative_point()
    return GeoPoint(lat=p.y, lon=p.x)


def hotspot_and_normal() -> tuple[str, str]:
    info = district_index().info
    hot = next(th for th, d in info.items() if d.hotspot)
    normal = next(th for th, d in info.items() if not d.hotspot)
    return hot, normal


def obs(r1: float, r6: float | None = None, age_min: int = 10) -> RainObservation:
    return RainObservation(source_id="s1", observed_at=NOW - timedelta(minutes=age_min), rain_1h_mm=r1, rain_6h_mm=r6)


def analyzer(**over) -> RuleBasedAnalyzer:
    return RuleBasedAnalyzer(cfg={**risk_thresholds(), **over}, now=lambda: NOW)


def test_level_from_boundaries():
    t = {"moderate": 10, "high": 30, "critical": 60}
    assert level_from(None, t) is RiskLevel.LOW
    assert level_from(9.9, t) is RiskLevel.LOW
    assert level_from(10, t) is RiskLevel.MODERATE
    assert level_from(30, t) is RiskLevel.HIGH
    assert level_from(60, t) is RiskLevel.CRITICAL


def test_max_of_1h_and_6h_rules_in_normal_district():
    _, normal = hotspot_and_normal()
    a = analyzer().assess(AlertInput(location=inside(normal), rain=[obs(5, 50)]))
    assert a.level is RiskLevel.HIGH and a.notify and a.district.th == normal  # 6h 50 >= 49
    a = analyzer().assess(AlertInput(location=inside(normal), rain=[obs(12, 20)]))
    assert a.level is RiskLevel.MODERATE and not a.notify


def test_max_across_sources():
    _, normal = hotspot_and_normal()
    a = analyzer().assess(AlertInput(location=inside(normal), rain=[obs(2), obs(35)]))
    assert a.level is RiskLevel.HIGH and a.rain_1h_mm == 35


def test_hotspot_escalates_one_level_but_not_from_low_or_above_critical():
    hot, _ = hotspot_and_normal()
    p = inside(hot)
    assert analyzer().assess(AlertInput(location=p, rain=[obs(12)])).level is RiskLevel.HIGH
    assert analyzer().assess(AlertInput(location=p, rain=[obs(5)])).level is RiskLevel.LOW
    assert analyzer().assess(AlertInput(location=p, rain=[obs(70)])).level is RiskLevel.CRITICAL
    assert analyzer(hotspot_escalation=False).assess(AlertInput(location=p, rain=[obs(12)])).level is RiskLevel.MODERATE


def test_stale_or_missing_rain_never_notifies():
    _, normal = hotspot_and_normal()
    a = analyzer().assess(AlertInput(location=inside(normal), rain=[obs(80, age_min=120)]))
    assert a.stale and a.level is RiskLevel.LOW and not a.notify
    assert analyzer().assess(AlertInput(location=inside(normal))).stale


def test_outside_bangkok_has_no_district_and_no_escalation():
    a = analyzer().assess(AlertInput(location=GeoPoint(lat=18.79, lon=98.98), rain=[obs(12)]))
    assert a.district is None and a.level is RiskLevel.MODERATE


def test_config_validation():
    with pytest.raises(ValueError, match="increase"):
        analyzer(rain_1h_mm={"moderate": 30, "high": 10, "critical": 60})
    with pytest.raises(ValidationError):
        RainObservation(source_id="x", observed_at=datetime(2026, 1, 1), rain_1h_mm=1)  # naive
    with pytest.raises(ValidationError):
        RainObservation(source_id="x", observed_at=NOW, rain_1h_mm=-1)


def test_district_index_covers_50_districts():
    idx = DistrictIndex()
    assert len(idx.info) == 50 and len(idx._shapes) == 50
    for th in list(idx.info)[:10]:
        assert idx.locate(inside(th)).th == th


def test_rain_alert_card_wording():
    hot, _ = hotspot_and_normal()
    a = analyzer().assess(AlertInput(location=inside(hot), rain=[obs(35, 55)]))
    card = build_rain_alert(a)
    flat = str(card)
    assert card["type"] == "flex" and "ไม่ใช่การพยากรณ์น้ำท่วม" in flat and f"เขต{hot}" in flat
    assert len(card["altText"]) <= 400


def test_assess_endpoint(monkeypatch):
    monkeypatch.setenv("B2B_API_KEYS", "k1")
    _, normal = hotspot_and_normal()
    p = inside(normal)
    body = {
        "location": {"lat": p.lat, "lon": p.lon},
        "rain": [{"source_id": "s", "observed_at": datetime.now(UTC).isoformat(), "rain_1h_mm": 31}],
    }
    r = TestClient(server.app).post("/v1/risk/assess", json=body, headers={"X-API-Key": "k1"})
    assert r.status_code == 200 and r.json()["level"] == "high" and r.json()["notify"] is True
