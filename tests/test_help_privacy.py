import asyncio
import json
import struct
from datetime import UTC, datetime

import httpx
import pytest

from src.alerts import help_messages as hm
from src.alerts import rich_menu as rmenu
from src.api import server
from tests.test_community import _loc, line  # noqa: F401  (pytest fixture)
from tests.test_risk_analyzer import hotspot_and_normal, inside


def test_follow_sends_welcome_with_menu_and_privacy_link(line, monkeypatch):  # noqa: F811
    _, send = line
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://nff.test")
    [w] = send({"type": "follow"})
    assert "น้ำ ฝน ฝุ่น" in w["text"] and "https://nff.test/privacy" in w["text"] and "1784" in w["text"]
    datas = [i["action"]["data"] for i in w["quickReply"]["items"]]
    assert datas == ["report=start", "route=start", "air=start", "home=start"]
    assert len(w["text"]) < 5000


def test_help_without_public_url_has_no_link(line, monkeypatch):  # noqa: F811
    _, send = line
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    [h] = send({"type": "message", "message": {"type": "text", "text": "วิธีใช้"}})
    assert "นโยบายความเป็นส่วนตัว" not in h["text"] and "ลบข้อมูลของฉัน" in h["text"]


def test_my_data_then_delete_everything(line, memory_db):  # noqa: F811
    _, send = line
    _, normal = hotspot_and_normal()
    p = inside(normal)
    send({"type": "postback", "postback": {"data": hm.POSTBACK_HOME}})
    [saved] = send(_loc(p))
    assert saved == hm.home_saved(normal)
    send({"type": "postback", "postback": {"data": "report=knee"}})
    send(_loc(p))
    memory_db.set_cooldown("U7", normal, "high", datetime.now(UTC))

    [mine] = send({"type": "message", "message": {"type": "text", "text": "ข้อมูลของฉัน"}})
    t = mine["text"]
    assert f"เขต{normal}" in t and "1 รายการ (ตัดการผูก" in t and "กันเตือนซ้ำ): 1" in t
    assert str(p.lat)[:6] not in t  # summary, never the coordinates themselves

    [ask] = send({"type": "message", "message": {"type": "text", "text": "ลบข้อมูลของฉัน"}})
    assert [i["action"]["data"] for i in ask["quickReply"]["items"]] == [hm.POSTBACK_DELETE, hm.POSTBACK_CANCEL]
    assert send({"type": "postback", "postback": {"data": hm.POSTBACK_CANCEL}}) == [hm.cancelled()]
    assert memory_db.user_count() == 1
    assert send({"type": "postback", "postback": {"data": hm.POSTBACK_DELETE}}) == [hm.deleted()]
    d = memory_db.user_data("U7", datetime(2000, 1, 1, tzinfo=UTC))
    assert d == hm.UserData(home_district=None, has_home=False, live_reports=0, alert_history=0, pending=None)
    assert memory_db.live_reports(datetime(2000, 1, 1, tzinfo=UTC)) == []


def test_home_button_cancels_a_pending_step(line, memory_db):  # noqa: F811
    _, send = line
    _, normal = hotspot_and_normal()
    send({"type": "message", "message": {"type": "text", "text": "เช็คเส้นทาง"}})
    send({"type": "postback", "postback": {"data": hm.POSTBACK_HOME}})
    [saved] = send(_loc(inside(normal)))
    assert "บันทึกแล้ว" in saved["text"] and memory_db.user_count() == 1


def test_privacy_page_and_contact_config(line, monkeypatch):  # noqa: F811
    client, _ = line
    page = client.get("/privacy")
    assert page.status_code == 200 and "ลบข้อมูลของฉัน" in page.text and "OSRM" in page.text
    cfg = client.get("/v1/public/app-config").json()
    assert cfg["name"] == "น้ำ ฝน ฝุ่น" and cfg["contact_email"] == "namfonfoon.th@gmail.com"
    monkeypatch.setitem(server.settings()["project"], "contact_email", "")
    assert client.get("/v1/public/app-config").json()["contact_email"] is None


# ---- rich menu ----


def test_rich_menu_tiles_the_image_and_every_button_is_handled(line):  # noqa: F811
    _, send = line
    m = rmenu.rich_menu("https://nff.test/map")
    cells = {(a["bounds"]["x"], a["bounds"]["y"], a["bounds"]["width"], a["bounds"]["height"]) for a in m["areas"]}
    assert sum(w * h for _, _, w, h in cells) == rmenu.WIDTH * rmenu.HEIGHT and len(cells) == 6
    assert max(x + w for x, _, w, _ in cells) == rmenu.WIDTH and max(y + h for _, y, _, h in cells) == rmenu.HEIGHT
    assert all(len(a["action"]["label"]) <= 20 for a in m["areas"]) and len(m["chatBarText"]) <= 14
    for a in m["areas"]:
        if a["action"]["type"] == "postback":
            assert send({"type": "postback", "postback": {"data": a["action"]["data"]}}), a["action"]["data"]
        else:
            assert a["action"]["uri"] == "https://nff.test/map"
    with pytest.raises(ValueError):
        rmenu.rich_menu("")


def test_rich_menu_image_fits_line_limits():
    data = rmenu.IMAGE.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) <= rmenu.MAX_IMAGE_BYTES
    assert struct.unpack(">II", data[16:24]) == (rmenu.WIDTH, rmenu.HEIGHT)


def test_install_creates_uploads_and_sets_default():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/v2/bot/richmenu":
            return httpx.Response(200, json={"richMenuId": "rm-1"})
        return httpx.Response(200, json={})

    cfg = {
        "create_url": "https://api.line.test/v2/bot/richmenu",
        "content_url": "https://data.line.test/v2/bot/richmenu/{id}/content",
        "default_url": "https://api.line.test/v2/bot/user/all/richmenu/{id}",
    }
    menu = rmenu.rich_menu("https://nff.test/map")
    out = asyncio.run(rmenu.install(b"png", menu, token="tok", cfg=cfg, transport=httpx.MockTransport(handler)))
    assert out == "rm-1"
    assert [r.url.path for r in seen] == [
        "/v2/bot/richmenu",
        "/v2/bot/richmenu/rm-1/content",
        "/v2/bot/user/all/richmenu/rm-1",
    ]
    assert all(r.headers["authorization"] == "Bearer tok" for r in seen)
    assert seen[1].headers["content-type"] == "image/png" and json.loads(seen[0].content)["selected"] is True
    with pytest.raises(ValueError):
        asyncio.run(rmenu.install(b"x" * (rmenu.MAX_IMAGE_BYTES + 1), menu, token="tok", cfg=cfg))


def test_gitattributes_keep_pngs_binary():
    """deploy/** is forced to LF; without a later binary rule git corrupts richmenu.png (its header has CRLF)."""
    lines = [
        ln.split()[0]
        for ln in (rmenu.IMAGE.parents[2] / ".gitattributes").read_text().splitlines()
        if ln and ln[0] != "#"
    ]
    assert lines.index("*.png") > lines.index("deploy/**")
