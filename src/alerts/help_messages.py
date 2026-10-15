"""LINE replies for welcome/help, the home-alert signup and the user's own data (view / delete).

Self-service covers the PDPA rights to access and erase without anyone having to contact the project.
"""

from __future__ import annotations

from typing import Any

from src.alerts import air_messages as am
from src.alerts import report_messages as rm
from src.alerts import route_messages as rtm
from src.data.db import UserData

POSTBACK_HELP = "help=start"
POSTBACK_HOME = "home=start"
POSTBACK_DELETE = "privacy=delete"
POSTBACK_CANCEL = "privacy=cancel"


def _text(t: str, quick: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "text", "text": t}
    if quick:
        msg["quickReply"] = {"items": quick}
    return msg


def _postback(label: str, data: str) -> dict[str, Any]:
    return {"type": "action", "action": {"type": "postback", "label": label, "data": data, "displayText": label}}


def menu_buttons() -> list[dict[str, Any]]:
    return [
        _postback("🌊 รายงานน้ำ", rm.POSTBACK_PREFIX + "start"),
        _postback("🚗 เช็คเส้นทาง", rtm.POSTBACK_START),
        _postback("😷 ฝุ่น PM2.5", am.POSTBACK_START),
        _postback("🏠 เตือนแถวบ้าน", POSTBACK_HOME),
    ]


def help_text(name: str, privacy_url: str) -> str:
    t = (
        f"{name} 🌊🌧️😷 บริการฟรี ไม่มีโฆษณา ไม่ขายข้อมูล\n\n"
        "กดปุ่มด้านล่าง หรือพิมพ์:\n"
        "• รายงานน้ำ: บอกเพื่อนบ้านว่าน้ำท่วมถึงไหน\n"
        "• เช็คเส้นทาง: ดูฝน น้ำ และฝุ่นตลอดทาง\n"
        "• ฝุ่น: ค่า PM2.5 ใกล้ตัว (ทั่วประเทศ)\n"
        "• ระดับน้ำ: น้ำในเจ้าพระยาและคลอง เทียบกับตลิ่ง\n"
        "• แชร์ตำแหน่งบ้าน: รับเตือนเมื่อฝนตกหนักแถวบ้าน (กรุงเทพฯ)\n\n"
        '🔒 พิมพ์ "ข้อมูลของฉัน" เพื่อดูว่าเราเก็บอะไรไว้ หรือ "ลบข้อมูลของฉัน" เพื่อลบทั้งหมด'
    )
    if privacy_url:
        t += f"\nนโยบายความเป็นส่วนตัว: {privacy_url}"
    return t + ("\n\nข้อมูลทั้งหมดมาจากสถานีวัดและรายงานของประชาชน ไม่ใช่ประกาศทางการ เหตุฉุกเฉินโทร 1784 (ปภ.) หรือ 1555 (กทม.)")


def welcome(name: str, privacy_url: str) -> dict[str, Any]:
    return _text("สวัสดีครับ ยินดีต้อนรับสู่ " + help_text(name, privacy_url), menu_buttons())


def help_reply(name: str, privacy_url: str) -> dict[str, Any]:
    return _text(help_text(name, privacy_url), menu_buttons())


def ask_home() -> dict[str, Any]:
    return _text(
        "🏠 รับเตือนเมื่อฝนตกหนักแถวบ้าน\nกดปุ่ม 📍 แชร์ตำแหน่งบ้าน ด้านล่าง\n\n"
        "เราเก็บตำแหน่งแบบหยาบราว 1 กม. เพื่อเลือกสถานีวัดฝนใกล้บ้านเท่านั้น เปลี่ยนได้ทุกเมื่อด้วยการแชร์ใหม่",
        [{"type": "action", "action": {"type": "location", "label": "📍 แชร์ตำแหน่งบ้าน"}}],
    )


def home_saved(district: str | None) -> dict[str, Any]:
    where = f"เขต{district}" if district else "ตำแหน่งนี้"
    return _text(f"✅ บันทึกแล้ว จะเตือนเมื่อฝนตกหนักแถว{where}\nคำเตือนอ้างอิงจากปริมาณฝนที่สถานีวัดได้ ไม่ใช่การพยากรณ์น้ำท่วม")


_PENDING = {"report": "กำลังรายงานน้ำ", "route": "กำลังเช็คเส้นทาง", "air": "กำลังเช็คฝุ่น"}


def my_data(d: UserData) -> dict[str, Any]:
    home = (f"เขต{d.home_district}" if d.home_district else "มี") + " (ปัดให้หยาบราว 1 กม.)" if d.has_home else "ไม่มี"
    lines = [
        "🔒 ข้อมูลของคุณที่เราเก็บไว้",
        "• รหัสผู้ใช้ LINE (ไม่ใช่ชื่อ) สำหรับตอบกลับคุณ",
        f"• ตำแหน่งบ้านสำหรับรับเตือน: {home}",
        f"• รายงานน้ำที่ยังผูกกับคุณ: {d.live_reports} รายการ (ตัดการผูกเองใน 3 ชม.)",
        f"• ประวัติการเตือนล่าสุด (กันเตือนซ้ำ): {d.alert_history} รายการ",
    ]
    if d.pending:
        lines.append(f"• ขั้นตอนที่ค้างอยู่: {_PENDING.get(d.pending, d.pending)} (หายเองใน 10 นาที)")
    lines += ["", "เราไม่เก็บชื่อ รูป เบอร์โทร หรือประวัติการเดินทางของคุณ", 'พิมพ์ "ลบข้อมูลของฉัน" เพื่อลบทั้งหมด']
    return _text("\n".join(lines))


def confirm_delete() -> dict[str, Any]:
    return _text(
        "ลบข้อมูลของคุณทั้งหมดใช่ไหม?\nตำแหน่งบ้านจะถูกลบ และจะไม่ได้รับเตือนฝนอีกจนกว่าจะแชร์ใหม่",
        [_postback("🗑️ ยืนยันลบ", POSTBACK_DELETE), _postback("ยกเลิก", POSTBACK_CANCEL)],
    )


def deleted() -> dict[str, Any]:
    return _text(
        "✅ ลบข้อมูลของคุณแล้ว\nรายงานน้ำที่เคยส่งจะหายจากแผนที่ และไม่ผูกกับตัวคุณอีก\nอยากรับเตือนอีกเมื่อไร แชร์ตำแหน่งบ้านใหม่ได้เลย"
    )


def cancelled() -> dict[str, Any]:
    return _text("ยกเลิกแล้ว ข้อมูลของคุณยังอยู่เหมือนเดิม")
