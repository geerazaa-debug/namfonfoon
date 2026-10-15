import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from src.alerts import river_messages as rvm
from src.api import server
from src.data.thaiwater_level import RiverStation, fetch_levels, parse_waterlevel, river_levels
from src.data.validator import BoundingBox, GeoPoint
from tests.test_community import line  # noqa: F401  (pytest fixture)
from tests.test_risk_analyzer import hotspot_and_normal, inside

BOX = BoundingBox(min_lat=13.4, max_lat=14.1, min_lon=100.25, max_lon=100.95)


def row(code: str, lat: float, pct: str, sit: int, wl: str = "2.34", prev: str = "2.30") -> dict:
    return {
        "waterlevel_datetime": "2026-10-15 23:10",
        "waterlevel_msl": wl,
        "waterlevel_msl_previous": prev,
        "storage_percent": pct,
        "situation_level": sit,
        "station": {"tele_station_oldcode": code, "tele_station_name": {"th": f"สถานี {code}"},
                    "tele_station_lat": lat, "tele_station_long": 100.5},
        "geocode": {"province_name": {"th": "นนทบุรี"}},
    }  # fmt: skip


def test_parse_keeps_bangkok_box_and_trend():
    payload = {
        "waterlevel_data": {
            "data": [
                row("CPY014", 13.85, "98.99", 4),
                row("FLAT", 13.7, "50", 3, wl="1.00", prev="1.00"),
                row("FAR", 14.6, "131", 5),
                row("BAD", 13.7, "x", 3),
            ]
        }
    }
    a, b = parse_waterlevel(payload, BOX)
    assert (a.code, a.bank_percent, a.situation, a.rising, a.area) == ("CPY014", 98.99, 4, True, "นนทบุรี")
    assert a.observed_at == datetime(2026, 10, 15, 16, 10, tzinfo=UTC) and b.rising is None
    assert sorted(river_levels()) == [1, 2, 3, 4, 5]


def test_fetch_levels_parses_payload():
    t = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"waterlevel_data": {"data": [row("C.12", 13.8, "96", 4)]}})
    )
    cfg = {"url": "https://tw.test/wl", "timeout_s": 5, "max_retries": 0}
    [s] = asyncio.run(fetch_levels(cfg=cfg, transport=t))
    assert s.code == "C.12"


def station(code: str, lat: float, pct: float, sit: int, age_min: int = 30) -> RiverStation:
    return RiverStation(
        code=code, name=f"สถานี {code}", area="กรุงเทพมหานคร", location=GeoPoint(lat=lat, lon=100.5),
        observed_at=datetime.now(UTC) - timedelta(minutes=age_min), bank_percent=pct, situation=sit, rising=True,
    )  # fmt: skip


def test_river_reply_key_stations_then_nearest_home(line, memory_db, bma_feed):  # noqa: F811
    _, send = line
    bma_feed["river"] += [
        station("GLF001", 13.48, 33, 3),
        station("CPY014", 13.85, 117, 5),
        station("C.12", 13.80, 96, 4),
        station("OLD", 13.75, 50, 3, age_min=400),
        station("GLF002", 13.47, 40, 3, age_min=150),
    ]
    [r] = send({"type": "message", "message": {"type": "text", "text": "ระดับน้ำ"}})
    t = r["text"]
    assert t.index("CPY014") < t.index("C.12") < t.index("GLF001") and "OLD" not in t  # north -> south, fresh only
    assert "🔴 117% ของตลิ่ง · ล้นตลิ่ง ⬆️ กำลังขึ้น" in t and "ไม่ใช่ประกาศทางการ" in t
    assert "(2 ชม.ก่อน)" not in t  # stale lines show their age; GLF002 is not a key station

    _, normal = hotspot_and_normal()
    memory_db.upsert_user("U7", inside(normal), normal, datetime.now(UTC))
    [near] = send({"type": "postback", "postback": {"data": rvm.POSTBACK_START}})
    assert "ใกล้บ้านคุณ" in near["text"] and near["text"].count("% ของตลิ่ง") == 3 and "ห่าง" in near["text"]


def test_river_unavailable_and_public_endpoint(line, monkeypatch):  # noqa: F811
    client, send = line

    async def down() -> list:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(server, "fetch_levels", down)
    assert send({"type": "message", "message": {"type": "text", "text": "น้ำขึ้น"}}) == [rvm.unavailable()]
    data = client.get("/v1/public/river").json()
    assert data["stations"] == [] and len(data["levels"]) == 5
