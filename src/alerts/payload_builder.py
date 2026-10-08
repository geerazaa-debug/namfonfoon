"""Build LINE Flex Message alert cards."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from src.data.validator import GeoPoint
from src.engine.geofence import ZoneHit
from src.engine.risk_analyzer import Assessment, RiskLevel

LEVEL_COLORS: dict[str, str] = {
    RiskLevel.LOW: "#2E7D32",
    RiskLevel.MODERATE: "#F9A825",
    RiskLevel.HIGH: "#EF6C00",
    RiskLevel.CRITICAL: "#C62828",
}
LEVEL_TH: dict[str, str] = {
    RiskLevel.LOW: "ต่ำ",
    RiskLevel.MODERATE: "ปานกลาง",
    RiskLevel.HIGH: "สูง",
    RiskLevel.CRITICAL: "วิกฤต",
}


def maps_directions_url(origin: GeoPoint, destination: GeoPoint, waypoints: list[GeoPoint] | None = None) -> str:
    params = {
        "api": "1",
        "origin": f"{origin.lat},{origin.lon}",
        "destination": f"{destination.lat},{destination.lon}",
        "travelmode": "driving",
    }
    if waypoints:
        params["waypoints"] = "|".join(f"{w.lat},{w.lon}" for w in waypoints)
    return "https://www.google.com/maps/dir/?" + urlencode(params)


def text(t: str, **kw: Any) -> dict[str, Any]:
    return {"type": "text", "text": t, "wrap": True, **kw}


def build_flood_alert(hit: ZoneHit, user_location: GeoPoint, detour_url: str | None = None) -> dict[str, Any]:
    """Flex message for one zone hit. Caller supplies detour_url from a routing provider if available."""
    z = hit.zone
    color = LEVEL_COLORS.get(z.level, "#455A64")
    level_th = LEVEL_TH.get(z.level, z.level)
    where = "คุณอยู่ในพื้นที่เสี่ยง" if hit.distance_km == 0 else f"ห่างพื้นที่เสี่ยง {hit.distance_km:.1f} กม."
    alt = f"เตือนภัยน้ำท่วม ระดับ{level_th}: {where}"

    footer: list[dict[str, Any]] = [
        {
            "type": "button",
            "style": "secondary",
            "action": {
                "type": "uri",
                "label": "ดูพื้นที่เสี่ยงบนแผนที่",
                "uri": f"https://www.google.com/maps/search/?api=1&query={z.center.lat},{z.center.lon}",
            },
        }
    ]
    if detour_url:
        footer.insert(
            0,
            {
                "type": "button",
                "style": "primary",
                "color": color,
                "action": {"type": "uri", "label": "เส้นทางเลี่ยง", "uri": detour_url},
            },
        )

    return {
        "type": "flex",
        "altText": alt[:400],
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": color,
                "contents": [text(f"⚠ เตือนภัยน้ำท่วม: {level_th}", weight="bold", color="#FFFFFF", size="lg")],
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    text(where, weight="bold"),
                    text(f"คะแนนความเสี่ยง {z.risk_score:.0f}/100 · รัศมีโซน {z.radius_km:.1f} กม."),
                    text("หลีกเลี่ยงพื้นที่ต่ำ และติดตามประกาศจากหน่วยงานท้องถิ่น", size="sm", color="#666666"),
                ],
            },
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": footer},
        },
    }


def build_rain_alert(a: Assessment) -> dict[str, Any]:
    """Flex card for a rule-based rain advisory. Wording must not claim a flood prediction."""
    color = LEVEL_COLORS.get(a.level, "#455A64")
    level_th = LEVEL_TH.get(a.level, a.level)
    where = f"เขต{a.district.th}" if a.district else "พื้นที่ของคุณ"
    rain = f"ฝน 1 ชม. {a.rain_1h_mm:.0f} มม." if a.rain_1h_mm is not None else "ไม่มีข้อมูลฝนล่าสุด"
    if a.rain_6h_mm is not None:
        rain += f" · สะสม 6 ชม. {a.rain_6h_mm:.0f} มม."
    body = [text(f"{where}: {rain}", weight="bold")]
    if a.district and a.district.hotspot:
        roads = ", ".join(a.district.top_roads)
        body.append(text(f"เขตนี้มีถนนน้ำท่วมขังบ่อย (สถิติ กทม. 2565-68): {roads}", size="sm"))
    body.append(
        text("คำเตือนเชิงแนะนำจากปริมาณฝน ไม่ใช่การพยากรณ์น้ำท่วม · ข้อมูลจุดน้ำท่วม: สำนักการระบายน้ำ กทม.",
             size="xs", color="#666666")
    )  # fmt: skip
    return {
        "type": "flex",
        "altText": f"เตือนฝนตกหนัก ระดับ{level_th}: {where} {rain}"[:400],
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": color,
                "contents": [text(f"🌧 เตือนฝนตกหนัก: {level_th}", weight="bold", color="#FFFFFF", size="lg")],
            },
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
        },
    }
