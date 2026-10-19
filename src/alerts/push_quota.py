"""Monthly LINE push budget: count every push locally, stop before the plan limit, tell the owner early.

LINE's consumption API lags (hours), so a rain burst could overrun the quota before it shows; the local
count is the source of truth and LINE's figure only ever raises it. One slot is kept back for the
"quota exhausted" notice when an owner is configured. If LINE's API is unreachable the configured
fallback limit applies.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from src.config import settings
from src.data.db import Database

log = logging.getLogger(__name__)

BANGKOK = timezone(timedelta(hours=7))  # no DST; Windows has no tz database
WARNED, EXHAUSTED = 1, 2

QuotaFetch = Callable[[], Awaitable[tuple[int | None, int]]]
OwnerNotify = Callable[[str], Awaitable[object]]


def month_of(now: datetime) -> str:
    return now.astimezone(BANGKOK).strftime("%Y-%m")


def warn_text(used: int, limit: int) -> str:
    return (
        f"⚠️ น้ำ ฝน ฝุ่น: เดือนนี้ใช้โควต้าข้อความ push ไปแล้ว {used}/{limit} ({used * 100 // limit}%)\n"
        "ถ้าหมด ระบบจะหยุดส่งเตือนฝนจนถึงต้นเดือนหน้า ส่วนปุ่มเมนูและการตอบกลับยังใช้ได้ปกติ"
    )


def exhausted_text(used: int, limit: int) -> str:
    return (
        f"⛔ น้ำ ฝน ฝุ่น: โควต้าข้อความ push เดือนนี้หมดแล้ว ({used}/{limit})\n"
        "หยุดส่งเตือนฝนจนถึงต้นเดือนหน้า ปุ่มเมนูและการตอบกลับยังใช้ได้ปกติ"
    )


class PushQuota:
    def __init__(
        self,
        db: Database,
        *,
        fetch: QuotaFetch | None = None,
        notify_owner: OwnerNotify | None = None,
        cfg: dict | None = None,
    ) -> None:
        self.db = db
        self.fetch = fetch
        self.notify_owner = notify_owner
        cfg = cfg or settings()["line"]["push_quota"]
        self.fallback_limit = int(cfg["fallback_limit"])
        self.warn_pct = int(cfg["warn_pct"])
        self.reserve = 1 if notify_owner else 0
        self.month = ""
        self.limit: int | None = self.fallback_limit
        self.used = 0

    async def refresh(self, now: datetime) -> None:
        """Load this month's count and the plan limit. Call once per poll, before the first push."""
        self.month = month_of(now)
        self.used = self.db.push_usage(self.month)[0]
        self.limit = self.fallback_limit
        if self.fetch is None:
            return
        try:
            self.limit, line_used = await self.fetch()
        except Exception:
            log.warning("LINE quota API unreachable; using fallback limit %d", self.fallback_limit)
            return
        self.used = max(self.used, line_used)  # pushes sent outside this app (OA Manager) show here

    def allow(self) -> bool:
        return self.limit is None or self.used + 1 <= self.limit - self.reserve

    def record(self) -> None:
        self.db.add_push(self.month)
        self.used += 1

    async def check(self) -> None:
        """After a poll's pushes: at most one warn and one exhausted notice per month."""
        if self.limit is None or not self.month:
            return
        warned = self.db.push_usage(self.month)[1]
        if not self.allow() and warned < EXHAUSTED:
            await self._notice(exhausted_text(self.used, self.limit), EXHAUSTED)
        elif self.used * 100 >= self.warn_pct * self.limit and warned < WARNED:
            await self._notice(warn_text(self.used, self.limit), WARNED)

    async def _notice(self, text: str, level: int) -> None:
        log.warning("push quota: %d/%d used this month", self.used, self.limit)
        if self.notify_owner is not None:
            try:
                await self.notify_owner(text)
            except Exception:  # not retried: a full quota would make it fail every poll
                log.exception("owner quota notice failed")
            else:
                self.record()
        self.db.set_push_warned(self.month, level)
