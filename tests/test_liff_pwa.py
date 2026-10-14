import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from src.alerts.line_login import IdTokenClaims, InvalidIdToken, verify_id_token
from src.api import server
from src.config import settings
from src.data.validator import GeoPoint
from tests.test_risk_analyzer import hotspot_and_normal, inside

URL = "https://line.test/verify"


def _verify(handler) -> IdTokenClaims:
    return asyncio.run(verify_id_token("idt", "123", URL, transport=httpx.MockTransport(handler)))


def test_verify_id_token_sends_channel_and_checks_audience():
    seen: list[httpx.Request] = []

    def ok(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"sub": "U1", "aud": "123", "exp": 9999999999, "name": "x"})

    assert _verify(ok).sub == "U1"
    assert seen[0].content == b"id_token=idt&client_id=123"
    with pytest.raises(InvalidIdToken):
        _verify(lambda r: httpx.Response(400, json={"error": "invalid_request"}))
    with pytest.raises(InvalidIdToken):
        _verify(lambda r: httpx.Response(200, json={"sub": "U1", "aud": "999", "exp": 1}))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LINE_LOGIN_CHANNEL_ID", "123")

    async def fake_verify(token: str, channel_id: str, url: str) -> IdTokenClaims:
        if token != "good":
            raise InvalidIdToken
        return IdTokenClaims(sub="U5", aud=channel_id, exp=0)

    monkeypatch.setattr(server, "verify_id_token", fake_verify)
    return TestClient(server.app)


def _body(level: str = "knee", p=None) -> dict:
    p = p or inside(hotspot_and_normal()[1])
    return {"level": level, "location": {"lat": p.lat, "lon": p.lon}}


def test_web_report_auth_and_validation(client, monkeypatch, memory_db):
    good = {"Authorization": "Bearer good"}
    assert client.post("/v1/reports", json=_body()).status_code == 401
    assert client.post("/v1/reports", json=_body(), headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post("/v1/reports", json=_body("lava"), headers=good).status_code == 422
    r = client.post("/v1/reports", json=_body(), headers=good)
    assert r.status_code == 200 and r.json() == {"status": "stored", "district": hotspot_and_normal()[1]}
    [rep] = client.get("/v1/public/reports").json()["reports"]
    assert rep["level"] == "knee"
    chiang_mai = GeoPoint(lat=18.79, lon=98.98)
    outside = client.post("/v1/reports", json=_body(p=chiang_mai), headers=good)
    assert outside.json()["status"] == "outside_bangkok"
    monkeypatch.delenv("LINE_LOGIN_CHANNEL_ID")
    assert client.post("/v1/reports", json=_body(), headers=good).status_code == 503


def test_web_report_shares_rate_limit_with_chat(client, memory_db):
    limit = settings()["community_reports"]["max_reports_per_hour"]
    codes = [
        client.post("/v1/reports", json=_body(), headers={"Authorization": "Bearer good"}).status_code
        for _ in range(limit + 1)
    ]
    assert codes == [200] * limit + [429]
    assert {r.reporter for r in memory_db.live_reports(datetime.now(UTC) - timedelta(hours=1))} == {"U5"}


def test_app_config_and_pwa_assets(client, monkeypatch):
    monkeypatch.delenv("LIFF_ID", raising=False)
    cfg = client.get("/v1/public/app-config").json()
    assert cfg["liff_id"] is None and cfg["report_url"] is None and cfg["osrm_url"].startswith("https://")
    monkeypatch.setenv("LIFF_ID", "1234-abcd")
    assert client.get("/v1/public/app-config").json()["report_url"] == "https://liff.line.me/1234-abcd"

    m = client.get("/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    assert json.loads(m.content)["start_url"] == "/map"
    assert client.get("/sw.js").headers["content-type"].startswith("text/javascript")
    icon = client.get("/icons/icon-512.png")
    assert icon.status_code == 200 and icon.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/icons/..%2Fmap.html").status_code in (404, 422)
    page = client.get("/report")
    assert page.status_code == 200 and "liff" in page.text and "/v1/reports" in page.text
