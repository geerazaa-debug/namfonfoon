import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.alerts.line_notifier import LineNotifier, verify_signature
from src.alerts.payload_builder import build_flood_alert
from src.api import server
from src.data.validator import GeoPoint
from src.engine.geofence import FloodZone, haversine_km, zones_near_point, zones_on_route

CENTER = GeoPoint(lat=13.75, lon=100.50)
ZONE = FloodZone(zone_id="z1", center=CENTER, radius_km=1.0, risk_score=80, level="critical")


def test_haversine_one_degree_lat():
    assert haversine_km(GeoPoint(lat=0, lon=0), GeoPoint(lat=1, lon=0)) == pytest.approx(111.19, abs=0.05)


def test_point_inside_and_within_radius():
    inside = zones_near_point(CENTER, [ZONE])
    assert inside[0].distance_km == 0
    p = GeoPoint(lat=13.75 + 2.5 / 111.19, lon=100.50)  # ~2.5 km north -> 1.5 km from edge
    assert zones_near_point(p, [ZONE], buffer_km=1.0) == []
    hits = zones_near_point(p, [ZONE], buffer_km=2.0)
    assert hits[0].distance_km == pytest.approx(1.5, abs=0.01)


def test_route_crossing_zone_hits_even_if_endpoints_far():
    route = [GeoPoint(lat=13.75, lon=100.40), GeoPoint(lat=13.75, lon=100.60)]  # passes through center
    assert zones_on_route(route, [ZONE])[0].distance_km == 0


def test_route_clear_of_zone():
    route = [GeoPoint(lat=13.80, lon=100.40), GeoPoint(lat=13.80, lon=100.60)]  # ~5.5 km north
    assert zones_on_route(route, [ZONE], buffer_km=2.0) == []


def test_flex_payload_shape():
    hit = zones_near_point(CENTER, [ZONE])[0]
    msg = build_flood_alert(hit, CENTER, detour_url="https://maps.example/route")
    assert msg["type"] == "flex" and len(msg["altText"]) <= 400
    labels = [b["action"]["label"] for b in msg["contents"]["footer"]["contents"]]
    assert labels[0] == "เส้นทางเลี่ยง"


def test_line_push_sends_auth_and_retry_key():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})

    async def go():
        cfg = {"push_url": "https://api.line.me/v2/bot/message/push", "timeout_s": 5}
        async with LineNotifier(access_token="tok", cfg=cfg, transport=httpx.MockTransport(handler)) as n:
            return await n.push("U123", [{"type": "text", "text": "hi"}], retry_key="rk-1")

    assert asyncio.run(go()) == "rk-1"
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert seen[0].headers["X-Line-Retry-Key"] == "rk-1"
    assert json.loads(seen[0].content)["to"] == "U123"


def test_line_push_rejects_too_many_messages():
    async def go():
        async with LineNotifier(access_token="tok", cfg={"push_url": "https://x", "timeout_s": 5}) as n:
            await n.push("U1", [{"type": "text", "text": str(i)} for i in range(6)])

    with pytest.raises(ValueError):
        asyncio.run(go())


def _sign(body: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_verify_signature():
    assert verify_signature(b"{}", _sign(b"{}", "s"), "s")
    assert not verify_signature(b"{}", _sign(b"{}", "other"), "s")


def test_api_route_check_and_webhook(monkeypatch):
    monkeypatch.setenv("B2B_API_KEYS", "k1")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "sec")
    monkeypatch.setattr(server, "ZONES", {"z1": ZONE})
    monkeypatch.setattr(server, "SUBSCRIBERS", {})
    client = TestClient(server.app)

    route = {"route": [{"lat": 13.75, "lon": 100.40}, {"lat": 13.75, "lon": 100.60}]}
    assert client.post("/v1/route/check", json=route).status_code == 401
    r = client.post("/v1/route/check", json=route, headers={"X-API-Key": "k1"})
    assert r.status_code == 200 and r.json()["safe"] is False

    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "source": {"userId": "U1"},
                    "message": {"type": "location", "latitude": 13.7, "longitude": 100.5},
                }
            ]
        }
    ).encode()
    assert client.post("/webhook/line", content=body, headers={"X-Line-Signature": "bad"}).status_code == 401
    r = client.post("/webhook/line", content=body, headers={"X-Line-Signature": _sign(body, "sec")})
    assert r.status_code == 200
    assert server.SUBSCRIBERS["U1"] == GeoPoint(lat=13.7, lon=100.5)
