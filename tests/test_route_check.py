import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.alerts import route_messages as rtm
from src.api import server
from src.config import settings
from src.data.osrm import RoutePath, fetch_route
from src.data.validator import GeoPoint
from src.engine.community import MapReport, report_levels
from src.engine.districts import DistrictInfo
from src.engine.route_check import StationRain, check_route, locate_on_route, rain_class, rain_classes
from tests.test_community import _loc, line  # noqa: F401  (pytest fixture)
from tests.test_risk_analyzer import hotspot_and_normal, inside

NOW = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
A, B = GeoPoint(lat=13.75, lon=100.50), GeoPoint(lat=13.75, lon=100.60)  # ~10.8 km due east
OSRM_CFG = {"osrm_url": "https://osrm.test/route/v1/driving", "timeout_s": 5, "max_retries": 1}


def station(sid: str, lat: float, lon: float, mm: float, district: str | None = "ก", age_min: int = 20) -> StationRain:
    return StationRain(
        station_id=sid,
        location=GeoPoint(lat=lat, lon=lon),
        district=district,
        observed_at=NOW - timedelta(minutes=age_min),
        rain_1h_mm=mm,
    )


def report(level: str, lat: float, lon: float, confirmed: bool = False) -> MapReport:
    lv = report_levels()[level]
    return MapReport(
        lat=lat,
        lon=lon,
        district="ข",
        level=level,
        emoji=lv.emoji,
        label=lv.label,
        color=lv.color,
        reporters=2 if confirmed else 1,
        confirmed=confirmed,
        minutes_ago=15,
    )


def hot_east(p: GeoPoint) -> DistrictInfo:
    east = p.lon > 100.55
    return DistrictInfo(
        th="ตะวันออก" if east else "ตะวันตก",
        en="x",
        flood_days_2022_2025=0,
        median_depth_cm=None,
        top_roads=[],
        hotspot=east,
    )


def check(stations=(), reports=(), route=(A, B)):
    cfg = settings()["route_check"]
    return check_route(
        list(route),
        list(stations),
        list(reports),
        NOW,
        station_buffer_km=cfg["station_buffer_km"],
        report_buffer_m=cfg["report_buffer_m"],
        max_rain_age=timedelta(minutes=cfg["max_rain_age_min"]),
        warn_rain_mm=cfg["warn_rain_mm"],
        warn_report_rank=cfg["warn_report_rank"],
        levels=report_levels(),
        district_of=hot_east,
    )


def test_locate_on_route_gives_offset_and_distance_along():
    off, along = locate_on_route(GeoPoint(lat=13.76, lon=100.55), [A, B])
    assert off == pytest.approx(1.11, abs=0.02) and along == pytest.approx(5.4, abs=0.1)


def test_rain_class_picks_highest_matching():
    classes = rain_classes()
    assert rain_class(0.2, classes) is None
    assert rain_class(12, classes).key == "heavy" and rain_class(80, classes).key == "very_heavy"


def test_route_rain_keeps_wettest_fresh_nearby_gauge_per_district():
    out = check(
        [
            station("near", 13.755, 100.52, 3),
            station("near2", 13.745, 100.53, 7),  # same district, wetter -> wins
            station("far", 13.80, 100.52, 50),  # ~5.5 km off the route
            station("stale", 13.75, 100.51, 50, age_min=120),
            station("dry", 13.75, 100.58, 0, district="ค"),
        ]
    )
    assert out.stations_checked == 3
    assert [(x.district, x.rain_1h_mm, x.rain.key) for x in out.rain] == [("ก", 7, "moderate")]
    assert out.verdict == "caution" and out.distance_km == pytest.approx(10.8, abs=0.1)


def test_verdicts_and_reports_on_route():
    assert check().verdict == "no_data"
    assert check([station("dry", 13.75, 100.52, 0)]).verdict == "clear"
    assert check([station("wet", 13.75, 100.52, 15)]).verdict == "warn"
    out = check(
        reports=[report("ankle", 13.751, 100.57), report("clear", 13.75, 100.52), report("waist", 13.80, 100.5)]
    )
    assert [r.report.level for r in out.reports] == ["ankle"] and out.verdict == "caution"
    assert check(reports=[report("knee", 13.751, 100.53)]).verdict == "warn"
    assert out.hotspots == ["ตะวันออก"]  # sampled every 0.5 km, so a two-point route still crosses it


def test_fetch_route_parses_geojson_and_handles_no_route():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if "100.60000" in req.url.path:
            coords = [[100.5, 13.75], [100.55, 13.76], [100.6, 13.75]]
            return httpx.Response(200, json={"code": "Ok", "routes": [{"geometry": {"coordinates": coords}}]})
        return httpx.Response(200, json={"code": "NoRoute", "routes": []})

    t = httpx.MockTransport(handler)
    path = asyncio.run(fetch_route(A, B, cfg=OSRM_CFG, transport=t))
    assert [p.lon for p in path.points] == [100.5, 100.55, 100.6]
    assert seen[0].url.path.endswith("/100.50000,13.75000;100.60000,13.75000")
    assert seen[0].url.params["overview"] == "simplified"
    assert asyncio.run(fetch_route(A, GeoPoint(lat=13.7, lon=100.4), cfg=OSRM_CFG, transport=t)) is None


def test_failed_route_request_never_logs_coordinates(monkeypatch, caplog):
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("src.retry.asyncio.sleep", no_sleep)
    t = httpx.MockTransport(lambda req: httpx.Response(503))
    with caplog.at_level(logging.DEBUG), pytest.raises(httpx.HTTPStatusError):
        asyncio.run(fetch_route(A, B, cfg=OSRM_CFG, transport=t))
    assert "osrm.test" in caplog.text and "13.75" not in caplog.text


def test_db_pending_route_lifecycle(memory_db):
    since = NOW - timedelta(minutes=10)
    memory_db.set_pending_report("U1", "knee", NOW)
    memory_db.start_route("U1", NOW)  # cancels the half-done report
    assert memory_db.pop_pending_report("U1", since) is None
    assert memory_db.pending_route("U1", since).origin is None
    memory_db.set_route_origin("U1", GeoPoint(lat=13.123456, lon=100.987654), NOW, 3)
    assert memory_db.pending_route("U1", since).origin == GeoPoint(lat=13.123, lon=100.988)
    assert memory_db.pending_route("U1", NOW + timedelta(seconds=1)) is None  # expired -> dropped
    assert memory_db.pending_route("U1", since) is None

    memory_db.start_route("U1", NOW)
    memory_db.set_pending_report("U1", "knee", NOW)  # and a report cancels a route check
    assert memory_db.pending_route("U1", since) is None
    memory_db.start_route("U1", NOW)
    memory_db.delete_user("U1")
    assert memory_db.pending_route("U1", since) is None


def test_db_latest_rain_per_station(memory_db):
    memory_db.upsert_rain(
        [
            ("s1", NOW - timedelta(hours=2), 9.0, 13.7, 100.5, "ก"),
            ("s1", NOW - timedelta(hours=1), 4.0, 13.7, 100.5, "ก"),
            ("s2", NOW - timedelta(hours=5), 30.0, 13.8, 100.6, None),
        ]
    )
    [s] = memory_db.latest_rain(NOW - timedelta(hours=3))
    assert (s.station_id, s.rain_1h_mm, s.district) == ("s1", 4.0, "ก")


# ---- LINE flow ----


@pytest.fixture
def fake_osrm(monkeypatch):
    calls: list[tuple[GeoPoint, GeoPoint]] = []

    async def fake(a: GeoPoint, b: GeoPoint) -> RoutePath | None:
        calls.append((a, b))
        return RoutePath(points=[a, b])

    monkeypatch.setattr(server, "fetch_route", fake)
    return calls


def test_webhook_route_flow_end_to_end(line, memory_db, fake_osrm, monkeypatch):  # noqa: F811
    client, send = line
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://fw.test/")
    hot, normal = hotspot_and_normal()
    a, b = inside(normal), inside(hot)
    now = datetime.now(UTC)
    memory_db.upsert_rain([("s1", now - timedelta(minutes=30), 12.0, a.lat, a.lon, normal)])
    memory_db.add_report("U9", "knee", b, hot, now, 3)

    [ask] = send({"type": "message", "message": {"type": "text", "text": "เช็คเส้นทาง"}})
    assert ask["quickReply"]["items"][0]["action"]["type"] == "location"
    [ask_dest] = send(_loc(a))
    assert ask_dest == rtm.ask_destination()
    [res] = send(_loc(b))
    text = res["text"]
    assert "⚠️" in text and f"เขต{normal}" in text and "ฝนหนัก 12 มม." in text and "ถึงเข่า" in text
    assert "ไม่ใช่การพยากรณ์" in text and "https://fw.test/map#from=" in text and len(text) < 5000
    assert res["quickReply"]["items"][0]["action"]["data"] == rtm.POSTBACK_START
    coarse = [GeoPoint(lat=round(p.lat, 3), lon=round(p.lon, 3)) for p in (a, b)]
    assert list(fake_osrm[0]) == coarse  # OSRM sees both ends at ~110 m
    assert memory_db.user_count() == 0  # route points are not a home subscription

    assert "บันทึกแล้ว" in send(_loc(a))[0]["text"] and memory_db.user_count() == 1  # flow over -> subscribes


def test_webhook_route_falls_back_to_straight_line_and_rejects_far_points(line, memory_db, monkeypatch):  # noqa: F811
    _, send = line

    async def broken(a: GeoPoint, b: GeoPoint) -> RoutePath | None:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(server, "fetch_route", broken)
    hot, normal = hotspot_and_normal()
    send({"type": "postback", "postback": {"data": rtm.POSTBACK_START}})
    send(_loc(inside(normal)))
    [res] = send(_loc(inside(hot)))
    assert "เส้นตรง" in res["text"] and "❔" in res["text"]  # no gauges seeded -> no data

    send({"type": "message", "message": {"type": "text", "text": "เส้นทาง"}})
    assert send(_loc(GeoPoint(lat=18.79, lon=98.98))) == [rtm.outside_area()]  # Chiang Mai
    assert memory_db.pending_route("U7", datetime.now(UTC) - timedelta(minutes=10)) is None


def test_public_rain_lists_only_fresh_wet_gauges(line, memory_db):  # noqa: F811
    client, _ = line
    now = datetime.now(UTC)
    memory_db.upsert_rain(
        [
            ("wet", now - timedelta(minutes=30), 6.0, 13.7, 100.5, "ก"),
            ("dry", now - timedelta(minutes=30), 0.0, 13.8, 100.6, "ข"),
            ("old", now - timedelta(hours=3), 40.0, 13.9, 100.7, "ค"),
        ]
    )
    data = client.get("/v1/public/rain").json()
    assert [(s["district"], s["rain"]["key"]) for s in data["stations"]] == [("ก", "moderate")]
    assert "station_id" not in data["stations"][0] and data["classes"][0]["key"] == "light"
    assert client.get("/map").text.count("/v1/public/rain") >= 1


def test_failed_webhook_event_logs_no_coordinates(line, monkeypatch, caplog):  # noqa: F811
    _, send = line

    async def boom(a: GeoPoint, b: GeoPoint, now: datetime) -> None:
        raise ValueError(f"bad point {a.lat}")

    monkeypatch.setattr(server, "run_route_check", boom)
    _, normal = hotspot_and_normal()
    p = inside(normal)
    send({"type": "postback", "postback": {"data": rtm.POSTBACK_START}})
    send(_loc(p))
    with caplog.at_level(logging.ERROR):
        assert send(_loc(p)) == []
    assert "ValueError" in caplog.text and f"{p.lat:.3f}"[:6] not in caplog.text
