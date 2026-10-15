"""LINE reply for "ระดับน้ำ": key Chao Phraya stations, or the ones nearest a subscriber's home."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.data.thaiwater_level import BKK_TZ, RiverLevel, RiverStation

POSTBACK_START = "river=start"


def _line(s: RiverStation, levels: dict[int, RiverLevel], now: datetime, km: float | None = None) -> str:
    lv = levels[s.situation]
    trend = "" if s.rising is None else " ⬆️ กำลังขึ้น" if s.rising else " ⬇️ กำลังลง"
    dist = f" (ห่าง {km:.1f} กม.)" if km is not None else ""
    when = s.observed_at.astimezone(BKK_TZ).strftime("%H:%M")
    hours = int((now - s.observed_at).total_seconds() // 3600)
    age = f" ({hours} ชม.ก่อน)" if hours >= 1 else ""
    return f"• {s.name} {s.area}{dist}\n  {lv.emoji} {s.bank_percent:.0f}% ของตลิ่ง · {lv.label}{trend} · {when} น.{age}"


def result(
    stations: list[tuple[RiverStation, float | None]],
    levels: dict[int, RiverLevel],
    *,
    near_home: bool,
    map_url: str,
    now: datetime,
) -> dict[str, Any]:
    head = "🌊 ระดับน้ำแม่น้ำและคลอง ใกล้บ้านคุณ" if near_home else "🌊 ระดับน้ำเจ้าพระยา (เหนือ → ใต้)"
    lines = [head, ""] + [_line(s, levels, now, km) for s, km in stations]
    if map_url:
        lines += ["", f"🗺️ ดูทุกสถานี: {map_url}"]
    lines += [
        "",
        "ข้อมูล: คลังข้อมูลน้ำแห่งชาติ (ThaiWater) · สถานะตามเกณฑ์ของ ThaiWater · ไม่ใช่ประกาศทางการ",
        "ชุมชนริมน้ำติดตามประกาศของ กทม. และ ปภ. 1784",
    ]
    return {"type": "text", "text": "\n".join(lines)}


def unavailable() -> dict[str, Any]:
    return {"type": "text", "text": "ขออภัยครับ ตอนนี้ดึงข้อมูลระดับน้ำไม่สำเร็จ ลองใหม่อีกครั้งในอีกสักครู่"}
