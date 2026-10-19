import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from src.alerts.line_notifier import LineNotifier
from src.alerts.push_quota import PushQuota, month_of
from src.data.db import Database
from tests.test_rain_pipeline import NOW, make_pipeline

CFG = {"fallback_limit": 3, "warn_pct": 60}


def test_month_is_bangkok_time():
    assert month_of(datetime(2026, 10, 31, 17, 30, tzinfo=UTC)) == "2026-11"


def test_pipeline_stops_at_the_local_count_and_warns_the_owner_once():
    """Limit 3, one slot reserved for the owner: 2 alerts, warn at 2/3, then exhausted notice uses the last slot."""
    db = Database()
    owner: list[str] = []

    async def notify(text):
        owner.append(text)

    async def fetch():
        return 3, 0  # LINE's consumption lags: still says 0

    sent = []

    async def send(uid, msgs):
        sent.append(uid)

    subs = {}
    pipe, loc, _ = make_pipeline(subs, 35, send)
    subs.update({"U1": loc, "U2": loc, "U3": loc})
    pipe.quota = PushQuota(db, fetch=fetch, notify_owner=notify, cfg=CFG)
    r = asyncio.run(pipe.run_once(NOW))
    assert (r.sent, r.quota_skipped) == (2, 1) and len(sent) == 2
    assert len(owner) == 1 and "หมดแล้ว" in owner[0] and "2/3" in owner[0]
    assert db.push_usage(month_of(NOW)) == (3, 2)
    r2 = asyncio.run(pipe.run_once(NOW + timedelta(minutes=15)))
    assert r2.quota_skipped == 1 and len(owner) == 1  # U3 still waiting, no second notice


def test_warn_notice_and_line_usage_raises_the_count():
    db = Database()
    owner: list[str] = []

    async def notify(text):
        owner.append(text)

    async def fetch():
        return 10, 5  # 5 pushes sent from OA Manager this month

    q = PushQuota(db, fetch=fetch, notify_owner=notify, cfg=CFG)
    asyncio.run(q.refresh(NOW))
    q.record()
    asyncio.run(q.check())
    assert q.used == 7 and len(owner) == 1 and "6/10 (60%)" in owner[0]  # counted before the notice itself
    asyncio.run(q.check())
    assert len(owner) == 1


def test_unlimited_plan_and_unreachable_api():
    async def unlimited():
        return None, 999

    async def down():
        raise httpx.ConnectError("x")

    q = PushQuota(Database(), fetch=unlimited, cfg=CFG)
    asyncio.run(q.refresh(NOW))
    assert q.allow()
    q = PushQuota(Database(), fetch=down, cfg=CFG)
    asyncio.run(q.refresh(NOW))
    assert q.limit == 3 and q.reserve == 0
    for _ in range(3):
        assert q.allow()
        q.record()
    assert not q.allow()


def test_notifier_quota_parses_line_responses():
    def handler(req):
        if req.url.path.endswith("/consumption"):
            return httpx.Response(200, json={"totalUsage": 42})
        return httpx.Response(200, json={"type": "limited", "value": 300})

    cfg = {
        "timeout_s": 5,
        "quota_url": "https://api.line.test/q",
        "consumption_url": "https://api.line.test/q/consumption",
    }

    async def go():
        async with LineNotifier(access_token="t", cfg=cfg, transport=httpx.MockTransport(handler)) as n:
            return await n.quota()

    assert asyncio.run(go()) == (300, 42)
