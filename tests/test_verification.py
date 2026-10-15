import asyncio
from datetime import UTC, datetime, timedelta

from src.api import server
from src.data.bma_dds import parse_rain
from src.data.thaiwater_rain import RainAccumulator
from src.engine.districts import district_index
from src.engine.risk_analyzer import RainObservation, RuleBasedAnalyzer
from src.engine.route_check import RoadFlood, StationRain
from src.engine.verification import near_observations, snapshot
from tests.test_risk_analyzer import hotspot_and_normal

NOW = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)


def gauge(sid: str, p, mm: float, six: float | None = None) -> StationRain:
    return StationRain(
        station_id=sid, location=p, district=None, observed_at=NOW - timedelta(minutes=5), rain_1h_mm=mm, rain_6h_mm=six
    )


def test_district_points_lie_inside_their_district():
    idx = district_index()
    assert len(idx.points) == 50
    assert all(idx.locate(p).th == th for th, p in idx.points.items())


def test_bma_rain_keeps_6h_total():
    row = {"rain_code": "R", "latitude": 13.7, "longitude": 100.5, "site_timestamp": "/Date(1792094700000)/"}
    [s] = parse_rain([{**row, "rf1hr": 4.0, "rf6hr": 37.5, "status": 1}])
    assert s.rain_6h_mm == 37.5


def test_near_observations_radius_then_fallback():
    idx = district_index()
    _, normal = hotspot_and_normal()
    p = idx.points[normal]
    far = p.model_copy(update={"lat": p.lat + 0.07})  # ~7.8 km
    assert [o.source_id for o in near_observations(p, [gauge("far", far, 1)], 5, 10)] == ["far"]
    assert near_observations(p, [gauge("far", far, 1)], 5, 5) == []


def test_snapshot_compares_sources_and_reads_sensors():
    idx = district_index()
    hot, normal = hotspot_and_normal()
    p = idx.points[normal]
    tw_obs = [RainObservation(source_id="tw", observed_at=NOW - timedelta(minutes=10), rain_1h_mm=35)]
    flood = RoadFlood(sensor_id="F", name="x", district="ไม่ตรงชื่อ", location=p, observed_at=NOW, depth_cm=14)
    rows = snapshot(
        NOW,
        {normal: p, hot: idx.points[hot]},
        RuleBasedAnalyzer(now=lambda: NOW),
        lambda q: tw_obs if q == p else [],
        [gauge("b", p, 3, six=50)],
        [flood],
        idx.locate,
        radius_km=5,
        fallback_km=10,
    )
    by = {r.district: r for r in rows}
    assert (by[normal].tw_level, by[normal].tw_rain_1h) == ("high", 35)
    assert (by[normal].bma_level, by[normal].bma_rain_6h) == ("high", 50)  # 6 h rule from rf6hr
    assert (by[normal].sensors_online, by[normal].max_depth_cm) == (1, 14)  # matched by polygon, not BMA's name
    assert (by[hot].tw_level, by[hot].sensors_online, by[hot].max_depth_cm) == ("low", 0, None)


def test_collect_verification_writes_one_row_per_district(memory_db, bma_feed):
    acc = RainAccumulator(db=memory_db)
    now = datetime.now(UTC)
    _, normal = hotspot_and_normal()
    bma_feed["rain"].append(gauge("b", district_index().points[normal], 2).model_copy(update={"observed_at": now}))
    assert asyncio.run(server.collect_verification(acc, now)) == 50
    assert asyncio.run(server.collect_verification(acc, now)) == 50  # same poll time: replaced, not duplicated
    assert memory_db.verification_count() == 50
