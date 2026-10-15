"""The LINE OA rich menu: six big buttons in a 3x2 grid (deploy/richmenu/richmenu.png).

Run once per change, with the channel token in the environment:
    LINE_CHANNEL_ACCESS_TOKEN=... PUBLIC_BASE_URL=https://<domain> python -m src.alerts.rich_menu
It creates the menu, uploads the image and makes it the default for every user.
Location sharing is not a rich-menu action, so "home" is a postback that replies with a location button.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from src.alerts import air_messages as am
from src.alerts import help_messages as hm
from src.alerts import report_messages as rm
from src.alerts import route_messages as rtm
from src.config import settings
from src.retry import request_with_retry

WIDTH, HEIGHT = 2500, 1686
COLS = (833, 834, 833)
ROW_H = 843
MAX_IMAGE_BYTES = 1024 * 1024  # LINE limit
IMAGE = Path(__file__).resolve().parents[2] / "deploy" / "richmenu" / "richmenu.png"

# Same order as the cells in deploy/richmenu/richmenu.html: (label, postback data or None for the map link).
BUTTONS: list[tuple[str, str | None]] = [
    ("รายงานน้ำ", rm.POSTBACK_PREFIX + "start"),
    ("เช็คเส้นทาง", rtm.POSTBACK_START),
    ("ฝุ่น PM2.5", am.POSTBACK_START),
    ("แผนที่", None),
    ("เตือนแถวบ้าน", hm.POSTBACK_HOME),
    ("วิธีใช้", hm.POSTBACK_HELP),
]


def rich_menu(map_url: str) -> dict[str, Any]:
    if not map_url.startswith("https://"):
        raise ValueError("the map button needs an https URL (set PUBLIC_BASE_URL)")
    areas = []
    for i, (label, data) in enumerate(BUTTONS):
        row, col = divmod(i, 3)
        action: dict[str, Any] = (
            {"type": "uri", "label": label, "uri": map_url}
            if data is None
            else {"type": "postback", "label": label, "data": data, "displayText": label}
        )
        areas.append(
            {
                "bounds": {"x": sum(COLS[:col]), "y": row * ROW_H, "width": COLS[col], "height": ROW_H},
                "action": action,
            }
        )
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,  # open by default: older users may not find the chat-bar toggle
        "name": "namfonfoon-main",
        "chatBarText": "เปิดเมนู",
        "areas": areas,
    }


async def install(
    image: bytes,
    menu: dict[str, Any],
    *,
    token: str,
    cfg: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Create the menu, upload its PNG, set it as everyone's default. Returns the richMenuId."""
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError("rich menu image must be at most 1 MB")
    cfg = cfg or settings()["rich_menu"]
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30, transport=transport, headers=headers) as client:
        created = await request_with_retry(client, "POST", cfg["create_url"], json=menu, max_retries=1)
        menu_id = created.json()["richMenuId"]
        await request_with_retry(
            client,
            "POST",
            cfg["content_url"].format(id=menu_id),
            content=image,
            headers={"Content-Type": "image/png"},
            max_retries=1,
        )
        await request_with_retry(client, "POST", cfg["default_url"].format(id=menu_id), max_retries=1)
    return menu_id


def main() -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not token or not base:
        sys.exit("set LINE_CHANNEL_ACCESS_TOKEN and PUBLIC_BASE_URL")
    menu_id = asyncio.run(install(IMAGE.read_bytes(), rich_menu(base + "/map"), token=token))
    print("rich menu installed as default:", menu_id)


if __name__ == "__main__":
    main()
