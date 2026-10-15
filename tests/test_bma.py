import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.api import server
from src.data.bma_dds import fetch_floods, fetch_rain, parse_floods, parse_rain
from src.data.osrm import RoutePath
from src.data.snapshot_cache import SnapshotCache
from src.data.validator import GeoPoint
from src.engine.route_check import RoadFlood, StationRain
from tests.test_community import _loc, line  # noqa: F401  (pytest fixture)
from tests.test_risk_analyzer import hotspot_and_normal, inside
from tests.test_route_check import NOW, check

CFG = {
    "rain_url": "https://bma.test/rain",
    "flood_url": "https://bma.test/flood?id=0",
    "user_agent": "NamFonFoon/test",
    "timeout_s": 5,
    "max_retries": 0,
}


def raw_rain(code: str, mm: float, status: int = 1, ms: int = 1792094700000) -> dict:
    return {
        "rain_code": code,
        "latitude": 13.70791,
        "longitude": 100.58363,
        "site_timestamp": f"/Date({ms})/",
        "rf1hr": mm,
        "rf24hr": 18.5,
        "district_name": "คลองเตย",
        "status": status,
    }


FLOOD_PAYLOAD = {
    "floodTbl": [
        {"flood_id": 66, "flood_code": "FL.BBN.01", "typesite": 1, "flood": 15.0, "latitude": 13.67942,
         "longitude": 100.43563, "flood_short_name": "ถ.เอกชัย (ห้างบิ๊กซี)", "site_timestatmpTH": "16/10/2569 03:05"},
        {"flood_id": 67, "flood_code": "FL.X.02", "typesite": 1, "flood": 30.0, "latitude": 13.7,
         "longitude": 100.5, "flood_short_name": "เสีย", "site_timestatmpTH": "16/10/2569 03:05"},
        {"flood_id": 4, "flood_code": "TN.SLG.02", "typesite": 2, "flood": 5.0, "latitude": 13.74,
         "longitude": 100.61, "flood_short_name": "อุโมงค์", "site_timestatmpTH": "16/10/2569 03:05"},
        {"flood_id": 68, "flood_code": "FL.Y.03", "typesite": 1, "flood": 0.0, "latitude": 13.71,
         "longitude": 100.51, "flood_short_name": "วันที่เพี้ยน", "site_timestatmpTH": "bad"},
    ],
    "dtTbl": [
        {"flood_id": 66, "status": 1, "districtName": "บางบอน"},
        {"flood_id": 67, "status": 0, "districtName": "x"},  # faulty sensor
        {"flood_id": 4, "status": 1, "districtName": "สวนหลวง"},
        {"flood_id": 68, "status": 1, "districtName": "y"},
    ],
}  # fmt: skip


def test_parse_rain_keeps_online_gauges_with_utc_time():
    out = parse_rain([raw_rain("RF.KTY.01", 4.5), raw_rain("RF.OFF", 9, status=0), {"rain_code": "broken"}])
    [s] = out
    assert (s.station_id, s.rain_1h_mm, s.district) == ("bma:RF.KTY.01", 4.5, "คลองเตย")
    assert s.observed_at == datetime(2026, 10, 15, 20, 5, tzinfo=UTC)  # 03:05 Bangkok on the 16th


def test_parse_floods_keeps_online_road_sensors_in_buddhist_years():
    [f] = parse_floods(FLOOD_PAYLOAD)
    assert (f.sensor_id, f.depth_cm, f.district, f.name) == ("FL.BBN.01", 15.0, "บางบอน", "ถ.เอกชัย (ห้างบิ๊กซี)")
    assert f.observed_at == datetime(2026, 10, 15, 20, 5, tzinfo=UTC)


def test_fetchers_post_rain_get_floods_and_identify_themselves():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=[raw_rain("RF.A", 1)] if req.method == "POST" else FLOOD_PAYLOAD)

    t = httpx.MockTransport(handler)
    assert len(asyncio.run(fetch_rain(cfg=CFG, transport=t))) == 1
    assert len(asyncio.run(fetch_floods(cfg=CFG, transport=t))) == 1
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/rain"), ("GET", "/flood")]
    assert all(r.headers["user-agent"] == "NamFonFoon/test" for r in seen)


def test_snapshot_cache_ttl_fallback_and_reset():
    calls: list[int] = []

    async def fetch() -> list[int]:
        calls.append(1)
        if len(calls) == 2:
            raise httpx.ConnectError("down")
        return [len(calls)]

    c = SnapshotCache("t", fetch, lambda: timedelta(minutes=5))
    t0 = datetime(2026, 10, 16, tzinfo=UTC)
    assert asyncio.run(c.get(t0)) == [1] and asyncio.run(c.get(t0 + timedelta(minutes=4))) == [1]
    assert asyncio.run(c.get(t0 + timedelta(minutes=6))) == [1]  # fetch failed -> last copy
    assert asyncio.run(c.get(t0 + timedelta(minutes=7))) == [3]
    c.reset()
    assert asyncio.run(c.get(t0)) == [4]


def sensor(sid: str, lat: float, lon: float, depth: float, age_min: int = 5) -> RoadFlood:
    return RoadFlood(
        sensor_id=sid,
        name=f"ถนน {sid}",
        district="ก",
        location=GeoPoint(lat=lat, lon=lon),
        observed_at=NOW - timedelta(minutes=age_min),
        depth_cm=depth,
    )


def test_route_check_road_flood_sensors():
    def run(*floods: RoadFlood):
        return check(road_floods=list(floods))

    dry = run(sensor("dry", 13.7505, 100.52, 0))
    assert dry.verdict == "clear" and dry.flood_sensors_checked == 1 and dry.road_floods == []
    shallow = run(
        sensor("s", 13.7505, 100.52, 5), sensor("far", 13.76, 100.52, 40), sensor("old", 13.75, 100.53, 40, 60)
    )
    assert shallow.verdict == "caution" and [f.sensor.sensor_id for f in shallow.road_floods] == ["s"]
    assert run(sensor("deep", 13.7495, 100.55, 12)).verdict == "warn"
    assert run(sensor("noise", 13.7505, 100.52, 1)).road_floods == []


def _bma_rain(p: GeoPoint, mm: float) -> StationRain:
    return StationRain(
        station_id="bma:RF.T", location=p, district="ทดสอบ", observed_at=datetime.now(UTC) - timedelta(minutes=5),
        rain_1h_mm=mm,
    )  # fmt: skip


def test_webhook_route_uses_bma_rain_and_road_sensors(line, bma_feed, monkeypatch):  # noqa: F811
    _, send = line

    async def straight(a: GeoPoint, b: GeoPoint) -> RoutePath:
        return RoutePath(points=[a, b])

    monkeypatch.setattr(server, "fetch_route", straight)
    hot, normal = hotspot_and_normal()
    a, b = inside(normal), inside(hot)
    mid = GeoPoint(lat=(a.lat + b.lat) / 2, lon=(a.lon + b.lon) / 2)
    bma_feed["rain"].append(_bma_rain(a, 22))
    bma_feed["floods"].append(
        RoadFlood(sensor_id="FL.1", name="ถ.ทดสอบ", district="ทดสอบ", location=mid,
                  observed_at=datetime.now(UTC) - timedelta(minutes=5), depth_cm=15)
    )  # fmt: skip
    send({"type": "message", "message": {"type": "text", "text": "เช็คเส้นทาง"}})
    send(_loc(a))
    [res] = send(_loc(b))
    t = res["text"]
    assert "⚠️" in t and "ฝนหนัก 22 มม." in t and "🚧 น้ำท่วมถนน" in t and "ถ.ทดสอบ: น้ำลึก 15 ซม." in t
    assert "สำนักการระบายน้ำ" in t


def test_public_rain_and_road_flood_include_bma(line, bma_feed):  # noqa: F811
    client, _ = line
    now = datetime.now(UTC)
    bma_feed["rain"] += [_bma_rain(GeoPoint(lat=13.7, lon=100.5), 6), _bma_rain(GeoPoint(lat=13.8, lon=100.6), 0)]
    stale = _bma_rain(GeoPoint(lat=13.9, lon=100.6), 30).model_copy(update={"observed_at": now - timedelta(hours=2)})
    bma_feed["rain"].append(stale)
    bma_feed["floods"] += [
        RoadFlood(sensor_id=s, name=s, district=None, location=GeoPoint(lat=13.7, lon=100.5),
                  observed_at=now - timedelta(minutes=3), depth_cm=d)
        for s, d in (("wet", 8.0), ("dry", 0.0), ("deep", 25.0))
    ]  # fmt: skip
    rain = client.get("/v1/public/rain").json()["stations"]
    assert [(s["rain_1h_mm"], s["source"]) for s in rain] == [(6, "สำนักการระบายน้ำ กทม.")]
    rf = client.get("/v1/public/road-flood").json()
    assert rf["sensors_online"] == 3 and [f["name"] for f in rf["flooded"]] == ["deep", "wet"]


@pytest.mark.parametrize("path", ["/v1/public/road-flood", "/v1/public/rain"])
def test_public_endpoints_survive_bma_outage(line, monkeypatch, path):  # noqa: F811
    client, _ = line

    async def down() -> list:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(server, "fetch_bma_rain", down)
    monkeypatch.setattr(server, "fetch_bma_floods", down)
    assert client.get(path).status_code == 200
