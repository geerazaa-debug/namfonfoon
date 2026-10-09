import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from src.alerts.dispatcher import AlertPipeline, CooldownTracker
from src.api import server
from src.config import risk_thresholds
from src.data.thaiwater_rain import BKK_TZ, RainAccumulator, RainSnapshot, ThaiWaterRainClient, parse_rain_24h
from src.data.validator import BoundingBox, GeoPoint
from src.engine.risk_analyzer import RiskLevel, RuleBasedAnalyzer
from tests.test_risk_analyzer import hotspot_and_normal, inside

NOW = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)  # 19:30 Bangkok
BBOX = BoundingBox(min_lat=13.4, max_lat=14.1, min_lon=100.25, max_lon=100.95)
ACC_CFG = {"station_radius_km": 5, "fallback_radius_km": 10, "min_hours_for_6h": 5, "history_hours": 24}


def row(sid, lat, lon, rain_1h, dt="2026-10-08 19:00"):
    r = {"id": 1, "rain_24h": 10.0, "rainfall_datetime": dt, "station": {"id": sid, "tele_station_lat": lat,
         "tele_station_long": lon}}  # fmt: skip
    if rain_1h is not None:
        r["rain_1h"] = rain_1h
    return r


def snap(sid, p, mm, hours_ago=0):
    t = datetime(2026, 10, 8, 19, 0, tzinfo=BKK_TZ) - timedelta(hours=hours_ago)
    return RainSnapshot(station_id=sid, location=p, observed_at=t, rain_1h_mm=mm)


def test_parse_skips_missing_rain_and_outside_bbox_and_sets_tz():
    payload = {"data": [row(1, 13.75, 100.5, 12.5), row(2, 13.75, 100.5, None), row(3, 18.8, 98.9, 5.0)]}
    snaps = parse_rain_24h(payload, BBOX)
    assert [s.station_id for s in snaps] == ["1"]
    assert snaps[0].observed_at == datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def test_client_fetches_and_parses():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [row(7, 13.7, 100.6, 3.0)]})

    async def go():
        cfg = {"rain_url": "https://example.test/rain_24h", "timeout_s": 5, "max_retries": 0}
        async with ThaiWaterRainClient(cfg=cfg, transport=httpx.MockTransport(handler)) as c:
            return await c.latest(BBOX)

    assert asyncio.run(go())[0].rain_1h_mm == 3.0


def test_rolling_6h_sum_needs_min_hours_and_does_not_double_count():
    p = GeoPoint(lat=13.75, lon=100.5)
    acc = RainAccumulator(ACC_CFG)
    acc.add([snap("a", p, 10, h) for h in range(4)])
    assert acc.observation("a").rain_6h_mm is None  # 4 of 6 hours < min 5
    acc.add([snap("a", p, 10, 4), snap("a", p, 10, 0)])  # hour 0 polled again: overwrite, not add
    o = acc.observation("a")
    assert o.rain_6h_mm == 50 and o.rain_1h_mm == 10
    acc.add([snap("a", p, 99, 30)])  # older than history window relative to latest -> pruned
    assert acc.observation("a").rain_6h_mm == 50


def test_station_selection_radius_then_fallback():
    p = GeoPoint(lat=13.75, lon=100.5)
    acc = RainAccumulator(ACC_CFG)
    acc.add([snap("near", GeoPoint(lat=13.76, lon=100.5), 1), snap("far", GeoPoint(lat=13.82, lon=100.5), 2)])
    assert [o.source_id for o in acc.near(p)] == ["thaiwater:near"]
    acc2 = RainAccumulator(ACC_CFG)
    acc2.add([snap("far", GeoPoint(lat=13.82, lon=100.5), 2)])  # ~7.8 km: fallback
    assert [o.source_id for o in acc2.near(p)] == ["thaiwater:far"]
    acc3 = RainAccumulator(ACC_CFG)
    acc3.add([snap("x", GeoPoint(lat=14.0, lon=100.5), 2)])  # ~28 km: nothing
    assert acc3.near(p) == []


def test_cooldown_suppresses_repeat_allows_escalation_and_expiry():
    cd = CooldownTracker(timedelta(hours=3))
    k = ("U1", "ดินแดง")
    assert cd.should_send(k, RiskLevel.HIGH, NOW)
    cd.record(k, RiskLevel.HIGH, NOW)
    assert not cd.should_send(k, RiskLevel.HIGH, NOW + timedelta(hours=1))
    assert cd.should_send(k, RiskLevel.CRITICAL, NOW + timedelta(hours=1))
    assert cd.should_send(k, RiskLevel.HIGH, NOW + timedelta(hours=3))
    cd.forget_user("U1")
    assert cd.should_send(k, RiskLevel.HIGH, NOW + timedelta(minutes=5))


def make_pipeline(subs, rain_mm, send):
    _, normal = hotspot_and_normal()
    loc = inside(normal)
    state = {"mm": rain_mm}

    async def fetch(bbox):
        return [snap("s1", loc, state["mm"])]

    pipe = AlertPipeline(
        fetch=fetch,
        subscribers=lambda: subs,
        send=send,
        analyzer=RuleBasedAnalyzer(cfg=risk_thresholds(), now=lambda: NOW),
        accumulator=RainAccumulator(ACC_CFG),
        cooldown=CooldownTracker(timedelta(minutes=180)),
        bbox=BBOX,
    )
    return pipe, loc, state


def test_pipeline_end_to_end_cooldown_and_escalation():
    sent = []

    async def send(uid, msgs):
        sent.append((uid, msgs[0]["altText"]))

    subs = {}
    pipe, loc, state = make_pipeline(subs, 35, send)  # 1h 35 mm -> HIGH in a normal district
    subs["U1"] = loc
    r1 = asyncio.run(pipe.run_once(NOW))
    assert (r1.sent, r1.notify) == (1, 1) and sent[0][0] == "U1" and "สูง" in sent[0][1]
    r2 = asyncio.run(pipe.run_once(NOW + timedelta(minutes=15)))
    assert (r2.sent, r2.suppressed_cooldown) == (0, 1)
    state["mm"] = 65  # escalates to CRITICAL inside the cooldown window
    r3 = asyncio.run(pipe.run_once(NOW + timedelta(minutes=30)))
    assert r3.sent == 1 and "วิกฤต" in sent[-1][1]


def test_failed_push_is_retried_next_poll_and_dry_run_never_sends():
    calls = {"n": 0}

    async def flaky(uid, msgs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LINE 500")

    subs = {}
    pipe, loc, _ = make_pipeline(subs, 35, flaky)
    subs["U1"] = loc
    assert asyncio.run(pipe.run_once(NOW)).failed == 1
    assert asyncio.run(pipe.run_once(NOW + timedelta(minutes=15))).sent == 1

    dry, loc2, _ = make_pipeline({"U2": loc}, 35, None)
    r = asyncio.run(dry.run_once(NOW))
    assert (r.notify, r.sent) == (1, 0)


def test_light_rain_and_no_station_do_not_notify():
    subs = {}
    pipe, loc, _ = make_pipeline(subs, 2, None)
    subs["U1"] = loc
    subs["U2"] = GeoPoint(lat=13.95, lon=100.9)  # no station within 10 km of the fake one
    r = asyncio.run(pipe.run_once(NOW))
    assert (r.notify, r.no_data) == (0, 1)


def test_server_lifespan_starts_dry_poller(monkeypatch):
    calls = []

    async def fake_fetch(bbox):
        calls.append(bbox)
        return []

    monkeypatch.setenv("POLLER_ENABLED", "1")
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(server, "default_fetch", lambda: fake_fetch)
    with TestClient(server.app) as c:
        assert c.get("/health").status_code == 200
        assert server.pipeline is not None and server.pipeline.send is None
    assert server.pipeline is None
