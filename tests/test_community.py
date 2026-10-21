import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from src.alerts import help_messages as hm
from src.alerts import report_messages as rm
from src.alerts.line_notifier import LineNotifier
from src.api import server
from src.config import settings
from src.data.validator import GeoPoint
from src.engine.community import CommunityReport, map_view, report_levels
from tests.test_risk_analyzer import hotspot_and_normal, inside

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
P = GeoPoint(lat=13.75, lon=100.50)
NEAR = GeoPoint(lat=13.7515, lon=100.50)  # ~170 m north
FAR = GeoPoint(lat=13.80, lon=100.50)  # ~5.5 km
KW = {"confirm_radius_m": 300, "confirm_min_reporters": 2, "ttl": timedelta(hours=3)}


def rep(who: str, level: str, p: GeoPoint = P, min_ago: int = 5) -> CommunityReport:
    return CommunityReport(
        reporter=who, level=level, location=p, district="x", reported_at=NOW - timedelta(minutes=min_ago)
    )


def test_levels_fit_line_quick_reply_limits():
    msg = rm.ask_level(report_levels())
    labels = [i["action"]["label"] for i in msg["quickReply"]["items"]]
    assert all(len(lb) <= 20 for lb in labels)
    assert labels[-1].endswith("น้ำลดแล้ว")  # deepest first, "gone" last


def test_map_view_keeps_latest_per_reporter_and_drops_expired():
    out = map_view(
        [rep("a", "waist", min_ago=60), rep("a", "clear", min_ago=5), rep("b", "knee", min_ago=200)], NOW, **KW
    )
    assert [(r.level, r.minutes_ago) for r in out] == [("clear", 5)]


def test_map_view_keeps_each_spot_and_never_self_confirms():
    out = map_view([rep("a", "knee", P), rep("a", "knee", NEAR), rep("a", "waist", FAR)], NOW, **KW)
    assert len(out) == 3 and not any(r.confirmed for r in out)


def test_map_view_confirmation_needs_distinct_nearby_similar_reporters():
    out = {r.lat: r for r in map_view([rep("a", "knee"), rep("b", "waist", NEAR), rep("c", "knee", FAR)], NOW, **KW)}
    assert out[P.lat].confirmed and out[P.lat].reporters == 2
    assert not out[FAR.lat].confirmed and out[FAR.lat].reporters == 1
    # ankle vs waist differ by 2 ranks; "clear" never confirms a flood
    out = map_view([rep("a", "ankle"), rep("b", "waist", NEAR), rep("c", "clear", NEAR)], NOW, **KW)
    assert not any(r.confirmed for r in out)
    assert map_view([rep("a", "bogus")], NOW, **KW) == []


def test_db_pending_report_is_single_use_and_expires(memory_db):
    memory_db.set_pending_report("U1", "knee", NOW)
    assert memory_db.pop_pending_report("U1", NOW - timedelta(minutes=10)) == "knee"
    assert memory_db.pop_pending_report("U1", NOW - timedelta(minutes=10)) is None
    memory_db.set_pending_report("U1", "knee", NOW - timedelta(minutes=11))
    assert memory_db.pop_pending_report("U1", NOW - timedelta(minutes=10)) is None


def test_db_reports_round_coords_and_anonymize(memory_db):
    memory_db.add_report("U1", "knee", GeoPoint(lat=13.123456, lon=100.987654), "d", NOW, 3)
    [r] = memory_db.live_reports(NOW - timedelta(hours=3))
    assert (r.location.lat, r.location.lon) == (13.123, 100.988)
    assert memory_db.report_count("U1", NOW - timedelta(hours=1)) == 1
    memory_db.anonymize_reports(NOW + timedelta(seconds=1))
    assert memory_db.live_reports(NOW - timedelta(hours=3)) == []
    assert memory_db._query("SELECT COUNT(*) FROM community_reports WHERE reporter IS NULL")[0][0] == 1


def test_db_unfollow_anonymizes_reports_and_drops_pending(memory_db):
    memory_db.add_report("U1", "knee", P, "d", NOW, 3)
    memory_db.set_pending_report("U1", "ankle", NOW)
    memory_db.delete_user("U1")
    assert memory_db.live_reports(NOW - timedelta(hours=3)) == []
    assert memory_db.pop_pending_report("U1", NOW - timedelta(hours=1)) is None


def test_line_reply_posts_token_without_retry():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(500)

    async def go() -> None:
        async with LineNotifier(access_token="tok", cfg={"timeout_s": 5}, transport=httpx.MockTransport(handler)) as n:
            await n.reply("rt-1", [{"type": "text", "text": "hi"}], "https://line.test/reply")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert len(seen) == 1 and json.loads(seen[0].content)["replyToken"] == "rt-1"


# ---- webhook flow ----


def _sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(b"sec", body, hashlib.sha256).digest()).decode()


@pytest.fixture
def line(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "sec")
    replies: list[list[dict]] = []

    async def fake_reply(token: str, messages: list[dict]) -> None:
        replies.append(messages)

    monkeypatch.setattr(server, "send_reply", fake_reply)
    client = TestClient(server.app)

    def send(event: dict) -> list[dict]:
        replies.clear()
        source = {"type": "user", "userId": "U7"}
        body = json.dumps({"events": [{"replyToken": "rt", "source": source, **event}]}).encode()
        assert client.post("/webhook/line", content=body, headers={"X-Line-Signature": _sign(body)}).status_code == 200
        return replies[0] if replies else []

    return client, send


def _loc(p: GeoPoint) -> dict:
    return {"type": "message", "message": {"type": "location", "latitude": p.lat, "longitude": p.lon}}


def test_webhook_report_flow_end_to_end(line, memory_db):
    client, send = line
    _, normal = hotspot_and_normal()
    spot = inside(normal)

    [ask] = send({"type": "message", "message": {"type": "text", "text": " รายงานน้ำ "}})
    assert ask["quickReply"]["items"][0]["action"]["data"].startswith(rm.POSTBACK_PREFIX)
    [ask_loc] = send({"type": "postback", "postback": {"data": "report=knee"}})
    assert ask_loc["quickReply"]["items"][0]["action"]["type"] == "location"
    [thanks] = send(_loc(spot))
    assert normal in thanks["text"] and "ยังไม่ได้ตรวจสอบ" in thanks["text"]
    assert memory_db.user_count() == 0  # a report location is not a home subscription

    data = client.get("/v1/public/reports").json()
    [r] = data["reports"]
    assert r["level"] == "knee" and r["district"] == normal and not r["confirmed"]
    assert "reporter" not in r and "U7" not in json.dumps(data)
    assert [lv["key"] for lv in data["levels"]] == ["clear", "ankle", "knee", "waist"]

    [saved] = send(_loc(spot))  # no pending level -> plain share subscribes
    assert saved == hm.home_saved(normal) and memory_db.user_count() == 1


def test_webhook_skips_redelivered_events(line, memory_db):
    _, send = line
    _, normal = hotspot_and_normal()
    assert send({**_loc(inside(normal)), "deliveryContext": {"isRedelivery": True}}) == []
    assert memory_db.user_count() == 0


def test_webhook_rejects_outside_bangkok_and_rate_limits(line, memory_db):
    _, send = line
    _, normal = hotspot_and_normal()
    send({"type": "postback", "postback": {"data": "report=ankle"}})
    assert send(_loc(GeoPoint(lat=18.79, lon=98.98))) == [rm.outside_bangkok()]  # Chiang Mai

    limit = settings()["community_reports"]["max_reports_per_hour"]
    for _ in range(limit + 1):
        send({"type": "postback", "postback": {"data": "report=ankle"}})
        last = send(_loc(inside(normal)))
    assert last == [rm.too_many(limit)]
    assert memory_db.report_count("U7", datetime.now(UTC) - timedelta(hours=1)) == limit


def test_webhook_ignores_unknown_postback_and_map_page_serves(line):
    client, send = line
    assert send({"type": "postback", "postback": {"data": "report=lava"}}) == []
    assert send({"type": "postback", "postback": {"data": "report=start"}})[0]["quickReply"]
    r = client.get("/map")
    assert r.status_code == 200 and "/v1/public/reports" in r.text
