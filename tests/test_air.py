import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.alerts import air_messages as am
from src.api import server
from src.data.air4thai import AirStation, fetch_air, parse_air4thai
from src.data.osrm import RoutePath
from src.data.validator import GeoPoint
from src.engine.air import air_bands, air_on_route, nearest_air
from tests.test_community import _loc, line  # noqa: F401  (pytest fixture)
from tests.test_risk_analyzer import hotspot_and_normal, inside

NOW = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)  # 10:00 Bangkok
P = GeoPoint(lat=13.75, lon=100.50)
AGE = timedelta(minutes=180)


def raw(sid: str, lat: float, lon: float, pm: str, color: str, date: str = "2026-10-16", time: str = "09:00") -> dict:
    return {
        "stationID": sid,
        "nameTH": f"สถานี {sid}",
        "areaTH": "เขตทดสอบ, กรุงเทพฯ",
        "stationType": "BKK",
        "lat": str(lat),
        "long": str(lon),
        "AQILast": {"date": date, "time": time, "PM25": {"color_id": color, "aqi": "50", "value": pm}},
    }


def station(sid: str, lat: float, lon: float, pm: float, band: int, age_min: int = 60) -> AirStation:
    return AirStation(
        station_id=sid,
        name=f"สถานี {sid}",
        area="x",
        location=GeoPoint(lat=lat, lon=lon),
        observed_at=NOW - timedelta(minutes=age_min),
        pm25=pm,
        band=band,
    )


def test_parse_keeps_valid_pm25_only_in_bangkok_time():
    payload = {
        "stations": [
            raw("ok", 13.73, 100.48, "17.8", "2"),
            raw("missing", 13.7, 100.5, "-1", "0"),
            raw("nocolor", 13.7, 100.5, "20", "0"),
            {"stationID": "broken", "lat": "x", "long": "y", "AQILast": {}},
        ]
    }
    [s] = parse_air4thai(payload)
    assert (s.station_id, s.pm25, s.band) == ("ok", 17.8, 2)
    assert s.observed_at == datetime(2026, 10, 16, 2, 0, tzinfo=UTC)  # 09:00 +07


def test_fetch_air_uses_configured_url():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"stations": [raw("a", 13.7, 100.5, "40", "4")]})

    cfg = {"url": "https://air.test/aqi", "timeout_s": 5, "max_retries": 0}
    [s] = asyncio.run(fetch_air(cfg=cfg, transport=httpx.MockTransport(handler)))
    assert s.band == 4 and str(seen[0].url) == "https://air.test/aqi"


def test_bands_cover_thai_aqi_with_advice_for_both_groups():
    bands = air_bands()
    assert sorted(bands) == [1, 2, 3, 4, 5]
    assert all(b.general and b.sensitive for b in bands.values())


def test_nearest_air_skips_stale_and_far():
    stations = [station("old", 13.751, 100.501, 90, 5, age_min=300), station("near", 13.76, 100.50, 30, 3)]
    near = nearest_air(P, stations, NOW, max_km=15, max_age=AGE)
    assert near.station.station_id == "near" and near.distance_km == pytest.approx(1.1, abs=0.05)
    assert nearest_air(GeoPoint(lat=18.79, lon=98.98), stations, NOW, max_km=15, max_age=AGE) is None


def test_air_on_route_reports_worst_station_within_buffer():
    route = [P, GeoPoint(lat=13.75, lon=100.60)]
    stations = [
        station("a", 13.755, 100.52, 20, 2),
        station("b", 13.745, 100.58, 45, 4),
        station("far", 13.9, 100.55, 99, 5),
    ]
    out = air_on_route(route, stations, NOW, buffer_km=2, max_age=AGE)
    assert out.worst.station_id == "b" and out.stations == 2 and out.along_km == pytest.approx(8.6, abs=0.2)
    assert air_on_route(route, [], NOW, buffer_km=2, max_age=AGE) is None


def test_result_message_has_value_advice_and_source():
    near = nearest_air(P, [station("a", 13.75, 100.50, 45.4, 4)], NOW, max_km=15, max_age=AGE)
    text = am.result(near, air_bands()[4], "เด็ก ผู้สูงอายุ", at_home=True)["text"]
    assert "🟠 45 มคก./ลบ.ม." in text and "แถวบ้านคุณ" in text and "09:00 น." in text
    assert air_bands()[4].sensitive in text and "ไม่ใช่การพยากรณ์" in text


# ---- LINE flow ----


def _now_station(sid: str, p: GeoPoint, pm: float, band: int) -> AirStation:
    return AirStation(
        station_id=sid,
        name=f"สถานี {sid}",
        area="x",
        location=p,
        observed_at=datetime.now(UTC) - timedelta(minutes=30),
        pm25=pm,
        band=band,
    )


def test_webhook_air_lookup_then_check_elsewhere(line, memory_db, air_feed):  # noqa: F811
    _, send = line
    _, normal = hotspot_and_normal()
    p = inside(normal)
    air_feed.append(_now_station("s1", p, 52.0, 4))

    [ask] = send({"type": "message", "message": {"type": "text", "text": "PM 2.5"}})
    assert ask["quickReply"]["items"][0]["action"]["type"] == "location"
    [res] = send(_loc(p))
    assert "52 มคก." in res["text"] and "ใกล้ตำแหน่งที่ส่งมา" in res["text"]
    [again] = send(_loc(p))  # "📍 เช็คที่อื่น" stays a lookup
    assert "52 มคก." in again["text"]
    assert memory_db.user_count() == 0  # lookups are not home subscriptions


def test_webhook_air_uses_home_location_of_subscribers(line, memory_db, air_feed):  # noqa: F811
    _, send = line
    _, normal = hotspot_and_normal()
    p = inside(normal)
    memory_db.upsert_user("U7", p, normal, datetime.now(UTC))
    air_feed.append(_now_station("s1", p, 12.0, 1))
    [res] = send({"type": "postback", "postback": {"data": am.POSTBACK_START}})
    assert "แถวบ้านคุณ" in res["text"] and "🔵 12" in res["text"]


def test_webhook_air_without_data_or_station(line, monkeypatch):  # noqa: F811
    _, send = line

    async def down() -> list:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(server, "fetch_air", down)
    send({"type": "message", "message": {"type": "text", "text": "ฝุ่น"}})
    assert send(_loc(GeoPoint(lat=13.75, lon=100.5))) == [am.unavailable()]


def test_webhook_air_none_nearby(line, air_feed):  # noqa: F811
    _, send = line
    air_feed.append(_now_station("bkk", GeoPoint(lat=13.75, lon=100.5), 20, 2))
    send({"type": "message", "message": {"type": "text", "text": "ค่าฝุ่น"}})
    [res] = send(_loc(GeoPoint(lat=18.79, lon=98.98)))
    assert res == am.none_nearby(15)


def test_air_cache_fetches_once_and_keeps_last_copy(monkeypatch):
    calls: list[int] = []
    good = [station("a", 13.7, 100.5, 20, 2)]

    async def fetch() -> list:
        calls.append(1)
        if len(calls) > 1:
            raise httpx.ConnectError("down")
        return good

    monkeypatch.setattr(server, "fetch_air", fetch)
    assert asyncio.run(server.air_stations(NOW)) == good
    assert asyncio.run(server.air_stations(NOW + timedelta(minutes=5))) == good and len(calls) == 1
    assert asyncio.run(server.air_stations(NOW + timedelta(minutes=30))) == good and len(calls) == 2


def test_route_reply_includes_pm25_line(line, memory_db, air_feed, monkeypatch):  # noqa: F811
    _, send = line

    async def straight(a: GeoPoint, b: GeoPoint) -> RoutePath:
        return RoutePath(points=[a, b])

    monkeypatch.setattr(server, "fetch_route", straight)
    hot, normal = hotspot_and_normal()
    a, b = inside(normal), inside(hot)
    air_feed.append(_now_station("mid", GeoPoint(lat=(a.lat + b.lat) / 2, lon=(a.lon + b.lon) / 2), 80.0, 5))
    send({"type": "message", "message": {"type": "text", "text": "เช็คเส้นทาง"}})
    send(_loc(a))
    [res] = send(_loc(b))
    assert "😷 ฝุ่น PM2.5 ตามทาง" in res["text"] and "🔴 80" in res["text"]


def test_public_air_lists_fresh_stations(line, air_feed):  # noqa: F811
    client, _ = line
    air_feed.append(_now_station("fresh", GeoPoint(lat=13.7, lon=100.5), 30, 3))
    old = _now_station("old", GeoPoint(lat=13.8, lon=100.6), 30, 3)
    air_feed.append(old.model_copy(update={"observed_at": datetime.now(UTC) - timedelta(hours=5)}))
    data = client.get("/v1/public/air").json()
    assert [s["name"] for s in data["stations"]] == ["สถานี fresh"] and len(data["bands"]) == 5


def test_db_air_step_is_replaced_by_other_flows(memory_db):
    since = NOW - timedelta(minutes=10)
    memory_db.start_air("U1", NOW)
    assert memory_db.air_pending("U1", since) and memory_db.air_pending("U1", since)  # not consumed
    memory_db.set_pending_report("U1", "knee", NOW)
    assert not memory_db.air_pending("U1", since)
    memory_db.start_air("U1", NOW)
    assert memory_db.pop_pending_report("U1", since) is None and memory_db.air_pending("U1", since)


def test_tls_context_trusts_the_shipped_lets_encrypt_chain():
    from src.data.air4thai import tls_context

    subjects = [
        dict(x[0] for x in c["subject"]).get("commonName") for c in tls_context("ca/letsencrypt_yr.pem").get_ca_certs()
    ]
    assert "YR1" in subjects and "Root YR" in subjects and "ISRG Root X1" in subjects
