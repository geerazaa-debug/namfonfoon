"""LINE reply messages for the citizen report flow: pick a level, then share a location.

Every step is a button (quick reply) so it works without typing, for any age.
"""

from __future__ import annotations

from typing import Any

from src.engine.community import ReportLevel

POSTBACK_PREFIX = "report="


def _text(t: str, quick: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "text", "text": t}
    if quick:
        msg["quickReply"] = {"items": quick}
    return msg


def ask_level(levels: dict[str, ReportLevel]) -> dict[str, Any]:
    items = [
        {
            "type": "action",
            "action": {
                "type": "postback",
                "label": f"{lv.emoji} {lv.label}",
                "data": POSTBACK_PREFIX + lv.key,
                "displayText": f"{lv.emoji} {lv.label}",
            },
        }
        for lv in sorted(levels.values(), key=lambda x: -x.rank)
    ]
    return _text("🌊 น้ำตรงที่คุณอยู่ ถึงไหนแล้ว?\nกดเลือกปุ่มด้านล่างได้เลย", items)


def ask_location(level: ReportLevel, pending_min: int) -> dict[str, Any]:
    items = [{"type": "action", "action": {"type": "location", "label": "📍 ส่งตำแหน่ง"}}]
    return _text(
        f"{level.emoji} {level.label} รับทราบ\nกดปุ่ม 📍 ส่งตำแหน่ง ด้านล่าง เพื่อบอกว่าน้ำอยู่ตรงไหน (ภายใน {pending_min} นาที)",
        items,
    )


def thanks(level: ReportLevel, district: str, map_url: str) -> dict[str, Any]:
    t = f'🙏 ขอบคุณครับ รายงาน "{level.emoji} {level.label}" เขต{district} ขึ้นแผนที่แล้ว\nช่วยให้เพื่อนบ้านเลี่ยงน้ำได้'
    if map_url:
        t += f"\n\n🗺️ ดูแผนที่: {map_url}"
    return _text(t + "\n\nข้อมูลนี้เป็นรายงานจากประชาชน ยังไม่ได้ตรวจสอบโดยหน่วยงาน")


def outside_bangkok() -> dict[str, Any]:
    return _text("ขออภัยครับ ตอนนี้รับรายงานเฉพาะในกรุงเทพฯ ก่อน")


def too_many(limit: int) -> dict[str, Any]:
    return _text(f"รายงานครบ {limit} ครั้งในชั่วโมงนี้แล้ว ขอบคุณมากครับ ลองใหม่อีกครั้งภายหลังได้")
