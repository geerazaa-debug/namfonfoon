"""Build LINE Flex Message alert cards."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from src.data.validator import GeoPoint
from src.engine.geofence import ZoneHit
from src.engine.risk_analyzer import RiskLevel

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
