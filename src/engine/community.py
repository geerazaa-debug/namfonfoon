"""Citizen water-level reports: level catalogue and the public map view.

Reports are unverified observations from LINE users, not measurements. A report counts as
"confirmed" only when enough *distinct* reporters nearby saw a similar level.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

from pydantic import BaseModel, Field

from src.config import settings
from src.data.validator import GeoPoint
from src.engine.geofence import haversine_km


class ReportLevel(BaseModel):
    key: str
    rank: int = Field(ge=0)
    emoji: str
    label: str = Field(max_length=17)  # emoji + space + label <= LINE's 20-char quick-reply limit
    color: str


class CommunityReport(BaseModel):
    reporter: str
    level: str
    location: GeoPoint
    district: str
    reported_at: datetime


class MapReport(BaseModel):
    """Published shape: no reporter identity."""

    lat: float
    lon: float
    district: str
    level: str
    emoji: str
    label: str
    color: str
    reporters: int  # distinct reporters within confirm radius at a similar level, incl. this one
    confirmed: bool
    minutes_ago: int


@lru_cache(maxsize=1)
def report_levels() -> dict[str, ReportLevel]:
    return {lv["key"]: ReportLevel(**lv) for lv in settings()["community_reports"]["levels"]}


def map_view(
    reports: list[CommunityReport],
    now: datetime,
    *,
    confirm_radius_m: float,
    confirm_min_reporters: int,
    ttl: timedelta,
    levels: dict[str, ReportLevel] | None = None,
) -> list[MapReport]:
    """Latest live report per (reporter, spot), each annotated with how many distinct nearby reporters agree.

    Spots are the stored rounded coordinates, so a later "clear" at the same spot replaces "knee", while one
    person reporting two places keeps both. Agreement = level rank within +-1; one person never confirms themself.
    """
    levels = levels or report_levels()
    latest: dict[tuple[str, float, float], CommunityReport] = {}
    for r in reports:
        key = (r.reporter, r.location.lat, r.location.lon)
        if (
            r.level in levels
            and now - ttl <= r.reported_at <= now
            and (key not in latest or r.reported_at > latest[key].reported_at)
        ):
            latest[key] = r
    out: list[MapReport] = []
    for r in sorted(latest.values(), key=lambda x: x.reported_at, reverse=True):
        lv = levels[r.level]
        agree = len(
            {
                o.reporter
                for o in latest.values()
                if abs(levels[o.level].rank - lv.rank) <= 1
                and (o.level == "clear") == (r.level == "clear")  # "gone" never confirms "flooded"
                and haversine_km(o.location, r.location) * 1000 <= confirm_radius_m
            }
        )
        out.append(
            MapReport(
                lat=r.location.lat,
                lon=r.location.lon,
                district=r.district,
                level=lv.key,
                emoji=lv.emoji,
                label=lv.label,
                color=lv.color,
                reporters=agree,
                confirmed=agree >= confirm_min_reporters,
                minutes_ago=int((now - r.reported_at).total_seconds() // 60),
            )
        )
    return out
