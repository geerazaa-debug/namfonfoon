"""LINE replies for the PM2.5 lookup ("ฝุ่น"): the nearest fresh station, its band and plain advice."""

from __future__ import annotations

from typing import Any

from src.data.air4thai import BKK_TZ
from src.engine.air import AirBand, AirOnRoute, NearbyAir

POSTBACK_START = "air=start"
SOURCE = "ข้อมูล: กรมควบคุมมลพิษ (Air4Thai) รวมสถานีของ กทม. · ค่าที่วัดได้จริง ไม่ใช่การพยากรณ์"


def _text(t: str, quick: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "text", "text": t}
    if quick:
        msg["quickReply"] = {"items": quick}
    return msg


def _location_button(label: str) -> list[dict[str, Any]]:
    return [{"type": "action", "action": {"type": "location", "label": label}}]


def ask_location(pending_min: int) -> dict[str, Any]:
    return _text(
        f"😷 เช็คฝุ่น PM2.5\nกดปุ่ม 📍 ส่งตำแหน่ง ด้านล่าง เลือกตำแหน่งปัจจุบันหรือที่ที่จะไปก็ได้ (ภายใน {pending_min} นาที)",
        _location_button("📍 ส่งตำแหน่ง"),
    )


def result(near: NearbyAir, band: AirBand, sensitive_group: str, *, at_home: bool) -> dict[str, Any]:
    s = near.station
    where = "แถวบ้านคุณ" if at_home else "ใกล้ตำแหน่งที่ส่งมา"
    t = (
        f"😷 ฝุ่น PM2.5 {where}\n"
        f"{band.emoji} {s.pm25:.0f} มคก./ลบ.ม. · {band.label}\n"
        f"สถานี {s.name} (ห่าง {near.distance_km:.1f} กม.) วัดเมื่อ {s.observed_at.astimezone(BKK_TZ):%H:%M} น.\n\n"
        f"👤 คนทั่วไป: {band.general}\n"
        f"👵👶 {sensitive_group}: {band.sensitive}\n\n"
        f"{SOURCE}"
    )
    return _text(t, _location_button("📍 เช็คที่อื่น"))


def none_nearby(max_km: float) -> dict[str, Any]:
    return _text(f"ขออภัยครับ ไม่มีสถานีวัดฝุ่นที่มีข้อมูลล่าสุดภายใน {max_km:.0f} กม. จากตำแหน่งนี้")


def unavailable() -> dict[str, Any]:
    return _text("ขออภัยครับ ตอนนี้ดึงข้อมูลฝุ่นจากกรมควบคุมมลพิษไม่สำเร็จ ลองใหม่อีกครั้งในอีกสักครู่")


def route_line(air: AirOnRoute, band: AirBand) -> str:
    """One line for the route-check reply."""
    w = air.worst
    return (
        f"😷 ฝุ่น PM2.5 ตามทาง (สถานี {air.stations} แห่ง) สูงสุด {band.emoji} {w.pm25:.0f} มคก. {band.label}"
        f" แถว{w.name} (กม. {air.along_km:.0f})"
    )
