import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.alerts.dispatcher import AlertPipeline, CooldownTracker
from src.api import server
from src.config import risk_thresholds
from src.data.db import SCHEMA_VERSION, Database
from src.data.thaiwater_rain import BKK_TZ, RainAccumulator, RainSnapshot
from src.data.validator import BoundingBox, GeoPoint
from src.engine.risk_analyzer import RiskLevel, RuleBasedAnalyzer
from tests.test_risk_analyzer import hotspot_and_normal, inside

NOW = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
ACC_CFG = {"station_radius_km": 5, "fallback_radius_km": 10, "min_hours_for_6h": 5, "history_hours": 24}
P = GeoPoint(lat=13.75, lon=100.5)


def snap(sid, mm, hours_ago=0, p=P):
    t = datetime(2026, 10, 8, 19, 0, tzinfo=BKK_TZ) - timedelta(hours=hours_ago)
    return RainSnapshot(station_id=sid, location=p, observed_at=t, rain_1h_mm=mm)


def test_file_db_uses_wal_and_auto_creates_schema(tmp_path):
    path = tmp_path / "sub" / "fw.db"
    db = Database(path)
    assert path.exists()
    assert db.pragma("journal_mode") == "wal"
    assert db.pragma("user_version") == SCHEMA_VERSION
    db.close()
    db2 = Database(path)  # reopen: migration is idempotent
    assert db2.pragma("user_version") == SCHEMA_VERSION
    db2.close()


def test_rolling_6h_survives_restart(tmp_path):
    path = tmp_path / "fw.db"
    db = Database(path)
    RainAccumulator(ACC_CFG, db=db, district_of=lambda _: "ดินแดง").add([snap("a", 10, h) for h in range(6)])
    db.close()

    db2 = Database(path)  # "restart": brand-new process state
    o = RainAccumulator(ACC_CFG, db=db2).observation("a")
    assert o.rain_6h_mm == 60 and o.rain_1h_mm == 10
    assert db2._query("SELECT DISTINCT district_name FROM rain_logs")[0][0] == "ดินแดง"
    db2.close()


def test_same_instant_in_other_timezone_overwrites_not_duplicates():
    db = Database()
    t = datetime(2026, 10, 8, 19, 0, tzinfo=BKK_TZ)
    db.upsert_rain([("a", t, 5.0, 13.7, 100.5, None)])
    db.upsert_rain([("a", t.astimezone(timezone(timedelta(hours=-3))), 7.0, 13.7, 100.5, None)])
    assert db.rain_hours("a", since=t - timedelta(hours=1)) == {t.astimezone(UTC): 7.0}
    with pytest.raises(ValueError):
        db.upsert_rain([("a", datetime(2026, 10, 8, 12), 1.0, 13.7, 100.5, None)])  # naive


def test_prune_drops_rows_older_than_history():
    db = Database()
    acc = RainAccumulator(ACC_CFG, db=db)
    acc.add([snap("a", 1, 30)])
    acc.add([snap("a", 2, 0)])  # newest is 30 h after the first -> first is pruned
    assert len(db._query("SELECT * FROM rain_logs")) == 1


def test_cooldown_survives_restart(tmp_path):
    path = tmp_path / "fw.db"
    db = Database(path)
    CooldownTracker(timedelta(hours=3), db=db).record(("U1", "ดินแดง"), RiskLevel.HIGH, NOW)
    db.close()

    cd = CooldownTracker(timedelta(hours=3), db=Database(path))
    assert not cd.should_send(("U1", "ดินแดง"), RiskLevel.HIGH, NOW + timedelta(hours=1))
    assert cd.should_send(("U1", "ดินแดง"), RiskLevel.CRITICAL, NOW + timedelta(hours=1))
    assert cd.should_send(("U2", "ดินแดง"), RiskLevel.HIGH, NOW + timedelta(hours=1))  # per user
    assert cd.should_send(("U1", "ดินแดง"), RiskLevel.HIGH, NOW + timedelta(hours=3))


def test_users_rounded_upsert_and_delete_cascades_cooldowns():
    db = Database()
    db.upsert_user("U1", GeoPoint(lat=13.756789, lon=100.501234), "ราชเทวี", NOW)
    assert db.users() == {"U1": GeoPoint(lat=13.76, lon=100.5)}
    db.upsert_user("U1", GeoPoint(lat=13.70, lon=100.60), "สวนหลวง", NOW + timedelta(days=1))
    row = db._query("SELECT district_name, created_at, updated_at FROM users")[0]
    assert row[0] == "สวนหลวง" and row[1] < row[2]  # created_at kept, updated_at moved
    db.set_cooldown("U1", "สวนหลวง", "high", NOW)
    db.delete_user("U1")
    assert db.user_count() == 0 and db.get_cooldown("U1", "สวนหลวง") is None


def test_pipeline_does_not_realert_after_restart(tmp_path):
    path = tmp_path / "fw.db"
    _, normal = hotspot_and_normal()
    loc = inside(normal)
    sent = []

    async def send(uid, msgs):
        sent.append(uid)

    async def fetch(bbox):
        return [snap("s1", 35, 0, loc)]

    def build(db):
        db.upsert_user("U1", loc, normal, NOW)
        return AlertPipeline(
            fetch=fetch,
            subscribers=db.users,
            send=send,
            analyzer=RuleBasedAnalyzer(cfg=risk_thresholds(), now=lambda: NOW),
            accumulator=RainAccumulator(ACC_CFG, db=db),
            cooldown=CooldownTracker(timedelta(minutes=180), db=db),
            bbox=BoundingBox(min_lat=13.4, max_lat=14.1, min_lon=100.25, max_lon=100.95),
        )

    db = Database(path)
    assert asyncio.run(build(db).run_once(NOW)).sent == 1
    db.close()
    db2 = Database(path)  # crash + restart
    r = asyncio.run(build(db2).run_once(NOW + timedelta(minutes=20)))
    assert (r.sent, r.suppressed_cooldown) == (0, 1) and sent == ["U1"]
    db2.close()


def _sign(body: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_webhook_persists_user_and_unfollow_removes_user_and_cooldowns(monkeypatch, memory_db):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "sec")
    _, normal = hotspot_and_normal()
    loc = inside(normal)
    c = TestClient(server.app)
    msg = {"type": "location", "latitude": loc.lat, "longitude": loc.lon}
    follow = json.dumps({"events": [{"type": "message", "source": {"userId": "U9"}, "message": msg}]}).encode()
    assert (
        c.post("/webhook/line", content=follow, headers={"X-Line-Signature": _sign(follow, "sec")}).status_code == 200
    )
    assert memory_db._query("SELECT district_name FROM users WHERE line_user_id='U9'")[0][0] == normal
    memory_db.set_cooldown("U9", normal, "high", NOW)
    unfollow = json.dumps({"events": [{"type": "unfollow", "source": {"userId": "U9"}}]}).encode()
    assert (
        c.post("/webhook/line", content=unfollow, headers={"X-Line-Signature": _sign(unfollow, "sec")}).status_code
        == 200
    )
    assert memory_db.user_count() == 0 and memory_db.get_cooldown("U9", normal) is None
