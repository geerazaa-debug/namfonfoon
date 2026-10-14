"""LINE replies for the route check: share a start point, then a destination, get one summary.

Both steps are location buttons: LINE's picker lets people search a place name or drag the map.
"""

from __future__ import annotations

from typing import Any

from src.data.validator import GeoPoint
from src.engine.route_check import RouteCheck

POSTBACK_START = "route=start"
MAX_REPORT_LINES = 6  # keeps the reply far below LINE's 5,000-char text limit

_HEADLINE = {
    "warn": "⚠️ ระวัง มีฝนหนักหรือน้ำท่วมบางช่วง",
    "caution": "🌦️ มีฝนหรือน้ำบางช่วง",
    "clear": "✅ ยังไม่พบฝนหรือรายงานน้ำบนเส้นทาง",
    "no_data": "❔ ตอนนี้ไม่มีข้อมูลฝนล่าสุดจากสถานีใกล้เส้นทาง",
}


def _text(t: str, quick: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "text", "text": t}
    if quick:
        msg["quickReply"] = {"items": quick}
    return msg


def _location_button(label: str) -> list[dict[str, Any]]:
    return [{"type": "action", "action": {"type": "location", "label": label}}]


def _again() -> list[dict[str, Any]]:
    label = "🔁 เช็คเส้นทางอื่น"
    return [
        {"type": "action", "action": {"type": "postback", "label": label, "data": POSTBACK_START, "displayText": label}}
    ]


def ask_origin(pending_min: int) -> dict[str, Any]:
    return _text(
        "🚗 เช็คฝนและน้ำท่วมตามเส้นทาง\n\n"
        "ขั้นที่ 1 จาก 2: กดปุ่ม 📍 จุดเริ่มต้น ด้านล่าง\n"
        f"เลือกตำแหน่งปัจจุบัน หรือค้นหาชื่อสถานที่ก็ได้ (ภายใน {pending_min} นาที)",
        _location_button("📍 จุดเริ่มต้น"),
    )


def ask_destination() -> dict[str, Any]:
    return _text("ขั้นที่ 2 จาก 2: กดปุ่ม 🏁 ปลายทาง ด้านล่าง แล้วเลือกจุดที่จะไป", _location_button("🏁 ปลายทาง"))


def outside_area() -> dict[str, Any]:
    return _text("ขออภัยครับ ตอนนี้เช็คเส้นทางได้เฉพาะในกรุงเทพฯ และปริมณฑลใกล้เคียง", _again())


def _place(district: str | None, along_km: float) -> str:
    return f"กม. {along_km:.0f} " + (f"เขต{district}" if district else "นอกเขต กทม.")


def _mm(v: float) -> str:
    return f"{v:.1f}" if v < 10 else f"{v:.0f}"


def map_link(map_url: str, a: GeoPoint, b: GeoPoint) -> str:
    """Points go in the #fragment, which browsers never send to the server (nothing to log)."""
    return f"{map_url}#from={a.lat:.3f},{a.lon:.3f}&to={b.lat:.3f},{b.lon:.3f}"


def result(check: RouteCheck, *, straight_line: bool, map_url: str, a: GeoPoint, b: GeoPoint) -> dict[str, Any]:
    head = "🚗 ระยะตรง" if straight_line else "🚗 เส้นทางรถยนต์"
    lines = [f"{head} {check.distance_km:.1f} กม.", _HEADLINE[check.verdict]]
    if straight_line:
        lines.append("(หาเส้นทางถนนไม่สำเร็จ เลยเช็คตามแนวเส้นตรงแทน)")
    if check.rain:
        lines += ["", f"🌧️ ฝนชั่วโมงล่าสุด จากสถานีวัดใกล้เส้นทาง {check.stations_checked} แห่ง"]
        lines += [
            f"• {_place(x.district, x.along_km)}: {x.rain.emoji} {x.rain.label} {_mm(x.rain_1h_mm)} มม."
            for x in check.rain
        ]
    elif check.stations_checked:
        lines += ["", f"🌤️ สถานีวัดฝนใกล้เส้นทาง {check.stations_checked} แห่ง: ไม่มีฝน"]
    if check.reports:
        lines += ["", "🌊 รายงานน้ำจากประชาชน"]
        for x in check.reports[:MAX_REPORT_LINES]:
            r = x.report
            who = f"ยืนยัน {r.reporters} คน" if r.confirmed else "ผู้รายงาน 1 คน"
            lines.append(f"• {_place(r.district, x.along_km)}: {r.emoji} {r.label} ({who}, {r.minutes_ago} นาทีที่แล้ว)")
        if len(check.reports) > MAX_REPORT_LINES:
            lines.append(f"• และอีก {len(check.reports) - MAX_REPORT_LINES} จุด ดูได้บนแผนที่")
    if check.hotspots and check.verdict in ("warn", "caution"):
        lines += ["", "📍 ผ่านเขตที่น้ำท่วมบ่อย: " + ", ".join(check.hotspots)]
    if map_url:
        lines += ["", "🗺️ ดูบนแผนที่: " + map_link(map_url, a, b)]
    lines += ["", "ข้อมูลฝนวัดจริงจากสถานี ThaiWater และรายงานจากประชาชน ไม่ใช่การพยากรณ์"]
    return _text("\n".join(lines), _again())
