"""SQLite persistence: rain history, alert cooldowns, subscribers, citizen water reports.

WAL mode, one shared connection guarded by a lock (the poller and the webhook run in the same
process). Schema is created/migrated automatically via PRAGMA user_version.
User coordinates are stored rounded to 0.01 deg (~1.1 km): enough to pick rain stations within
5 km, without keeping an exact home location.
Citizen reports keep the reporter's LINE userId only while the report is live (rate limit and
distinct-reporter counting); `anonymize_reports` and `delete_user` null it afterwards.
`pending_steps` holds what a user's next shared location means (one row per user, so the latest intent wins):
a report level, a route check (with its coarse start point once shared) or a PM2.5 lookup.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from src.data.validator import GeoPoint
from src.engine.community import CommunityReport
from src.engine.route_check import StationRain

USER_COORD_DECIMALS = 2

_MIGRATIONS = {
    1: """
    CREATE TABLE rain_logs (
        station_id    TEXT NOT NULL,
        observed_at   TEXT NOT NULL,          -- ISO-8601 UTC, end of the 1 h window
        rain_1h       REAL NOT NULL CHECK (rain_1h >= 0),
        lat           REAL NOT NULL,
        lon           REAL NOT NULL,
        district_name TEXT,                   -- NULL outside Bangkok
        PRIMARY KEY (station_id, observed_at)
    );
    CREATE INDEX rain_logs_time ON rain_logs (observed_at);
    CREATE TABLE alert_cooldowns (
        line_user_id     TEXT NOT NULL,
        district_name    TEXT NOT NULL,       -- district, or rounded "lat,lon" outside Bangkok
        last_alert_level TEXT NOT NULL,
        last_alert_time  TEXT NOT NULL,
        PRIMARY KEY (line_user_id, district_name)
    );
    CREATE TABLE users (
        line_user_id  TEXT PRIMARY KEY,
        district_name TEXT,
        lat           REAL NOT NULL,
        lon           REAL NOT NULL,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    );
    """,
    2: """
    CREATE TABLE community_reports (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter      TEXT,                   -- LINE userId while live; NULL once anonymized
        level         TEXT NOT NULL,
        lat           REAL NOT NULL,          -- rounded to community_reports.coord_decimals
        lon           REAL NOT NULL,
        district_name TEXT NOT NULL,
        reported_at   TEXT NOT NULL
    );
    CREATE INDEX community_reports_time ON community_reports (reported_at);
    CREATE TABLE pending_reports (
        line_user_id TEXT PRIMARY KEY,
        level        TEXT NOT NULL,
        created_at   TEXT NOT NULL
    );
    """,
    3: """
    CREATE TABLE pending_routes (
        line_user_id TEXT PRIMARY KEY,
        origin_lat   REAL,                    -- NULL until the start point arrives
        origin_lon   REAL,
        created_at   TEXT NOT NULL
    );
    """,
    4: """
    CREATE TABLE pending_steps (
        line_user_id TEXT PRIMARY KEY,
        kind         TEXT NOT NULL,           -- report | route | air
        level        TEXT,                    -- report: the picked level
        lat          REAL,                    -- route: start point once shared (rounded)
        lon          REAL,
        created_at   TEXT NOT NULL
    );
    DROP TABLE pending_reports;
    DROP TABLE pending_routes;
    """,
}
SCHEMA_VERSION = max(_MIGRATIONS)


class UserData(BaseModel):
    """Everything stored about one LINE user, for "ข้อมูลของฉัน"."""

    home_district: str | None  # None: no district (outside Bangkok) or no home location
    has_home: bool
    live_reports: int  # still on the map and still linked to the user
    alert_history: int  # (district, last level) rows that stop repeat alerts
    pending: str | None  # report | route | air


class PendingRoute(BaseModel):
    origin: GeoPoint | None  # None: waiting for the start point


def ts(t: datetime) -> str:
    """Canonical UTC ISO string; lexicographic order == time order."""
    if t.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return t.astimezone(UTC).isoformat(timespec="seconds")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for v in sorted(k for k in _MIGRATIONS if k > version):
            self._conn.executescript(f"BEGIN; {_MIGRATIONS[v]} PRAGMA user_version = {v}; COMMIT;")

    def pragma(self, name: str) -> object:
        return self._query(f"PRAGMA {name}")[0][0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _write(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.executemany(sql, rows)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    # ---- rain_logs ----
    def upsert_rain(self, rows: list[tuple[str, datetime, float, float, float, str | None]]) -> None:
        """rows: (station_id, observed_at, rain_1h, lat, lon, district_name). Same station+hour overwrites."""
        self._write(
            "INSERT INTO rain_logs VALUES (?,?,?,?,?,?) ON CONFLICT(station_id, observed_at) DO UPDATE SET "
            "rain_1h=excluded.rain_1h, lat=excluded.lat, lon=excluded.lon, district_name=excluded.district_name",
            [(sid, ts(t), mm, lat, lon, d) for sid, t, mm, lat, lon, d in rows],
        )

    def rain_hours(self, station_id: str, since: datetime) -> dict[datetime, float]:
        rows = self._query(
            "SELECT observed_at, rain_1h FROM rain_logs WHERE station_id=? AND observed_at>=? ORDER BY observed_at",
            (station_id, ts(since)),
        )
        return {parse_ts(r[0]): r[1] for r in rows}

    def latest_rain_time(self, station_id: str) -> datetime | None:
        row = self._query("SELECT MAX(observed_at) FROM rain_logs WHERE station_id=?", (station_id,))[0][0]
        return parse_ts(row) if row else None

    def station_locations(self, since: datetime) -> dict[str, GeoPoint]:
        """Latest known location of every station reporting since `since`."""
        rows = self._query(
            "SELECT station_id, lat, lon FROM rain_logs r WHERE observed_at = "
            "(SELECT MAX(observed_at) FROM rain_logs WHERE station_id = r.station_id) AND observed_at >= ?",
            (ts(since),),
        )
        return {r[0]: GeoPoint(lat=r[1], lon=r[2]) for r in rows}

    def latest_rain(self, since: datetime) -> list[StationRain]:
        """Each station's latest hour, for stations reporting since `since`."""
        rows = self._query(
            "SELECT station_id, lat, lon, district_name, observed_at, rain_1h FROM rain_logs r WHERE observed_at = "
            "(SELECT MAX(observed_at) FROM rain_logs WHERE station_id = r.station_id) AND observed_at >= ?",
            (ts(since),),
        )
        return [
            StationRain(
                station_id=r[0],
                location=GeoPoint(lat=r[1], lon=r[2]),
                district=r[3],
                observed_at=parse_ts(r[4]),
                rain_1h_mm=r[5],
            )
            for r in rows
        ]

    def prune_rain(self, older_than: datetime) -> None:
        self._write("DELETE FROM rain_logs WHERE observed_at < ?", [(ts(older_than),)])

    # ---- alert_cooldowns ----
    def get_cooldown(self, user_id: str, area: str) -> tuple[datetime, str] | None:
        rows = self._query(
            "SELECT last_alert_time, last_alert_level FROM alert_cooldowns WHERE line_user_id=? AND district_name=?",
            (user_id, area),
        )
        return (parse_ts(rows[0][0]), rows[0][1]) if rows else None

    def set_cooldown(self, user_id: str, area: str, level: str, at: datetime) -> None:
        self._write(
            "INSERT INTO alert_cooldowns VALUES (?,?,?,?) ON CONFLICT(line_user_id, district_name) DO UPDATE SET "
            "last_alert_level=excluded.last_alert_level, last_alert_time=excluded.last_alert_time",
            [(user_id, area, level, ts(at))],
        )

    def delete_cooldowns(self, user_id: str) -> None:
        self._write("DELETE FROM alert_cooldowns WHERE line_user_id=?", [(user_id,)])

    # ---- users ----
    def upsert_user(self, user_id: str, location: GeoPoint, district: str | None, now: datetime) -> None:
        lat, lon = round(location.lat, USER_COORD_DECIMALS), round(location.lon, USER_COORD_DECIMALS)
        self._write(
            "INSERT INTO users VALUES (?,?,?,?,?,?) ON CONFLICT(line_user_id) DO UPDATE SET "
            "district_name=excluded.district_name, lat=excluded.lat, lon=excluded.lon, updated_at=excluded.updated_at",
            [(user_id, district, lat, lon, ts(now), ts(now))],
        )

    def delete_user(self, user_id: str) -> None:
        """Unfollow: drop subscription, cooldowns, pending step; keep past reports but anonymized."""
        self._write("DELETE FROM users WHERE line_user_id=?", [(user_id,)])
        self.delete_cooldowns(user_id)
        self.clear_step(user_id)
        self._write("UPDATE community_reports SET reporter=NULL WHERE reporter=?", [(user_id,)])

    def user_data(self, user_id: str, live_since: datetime) -> UserData:
        """What is stored about one user (PDPA access request), without the values themselves."""
        home = self._query("SELECT district_name FROM users WHERE line_user_id=?", (user_id,))
        step = self._query("SELECT kind FROM pending_steps WHERE line_user_id=?", (user_id,))
        cooldowns = self._query("SELECT COUNT(*) FROM alert_cooldowns WHERE line_user_id=?", (user_id,))[0][0]
        return UserData(
            home_district=home[0][0] if home else None,
            has_home=bool(home),
            live_reports=self.report_count(user_id, live_since),
            alert_history=int(cooldowns),
            pending=step[0][0] if step else None,
        )

    def user_location(self, user_id: str) -> GeoPoint | None:
        rows = self._query("SELECT lat, lon FROM users WHERE line_user_id=?", (user_id,))
        return GeoPoint(lat=rows[0][0], lon=rows[0][1]) if rows else None

    def users(self) -> dict[str, GeoPoint]:
        return {r[0]: GeoPoint(lat=r[1], lon=r[2]) for r in self._query("SELECT line_user_id, lat, lon FROM users")}

    def user_count(self) -> int:
        return int(self._query("SELECT COUNT(*) FROM users")[0][0])

    # ---- pending steps: what the next shared location means ----
    def _set_step(self, user_id: str, kind: str, now: datetime, level: str | None = None) -> None:
        self._write(
            "INSERT INTO pending_steps VALUES (?,?,?,NULL,NULL,?) ON CONFLICT(line_user_id) DO UPDATE SET "
            "kind=excluded.kind, level=excluded.level, lat=NULL, lon=NULL, created_at=excluded.created_at",
            [(user_id, kind, level, ts(now))],
        )

    def _step(self, user_id: str, kind: str, since: datetime) -> sqlite3.Row | None:
        """The user's pending step if it is `kind`; an expired one is dropped, another kind is left alone."""
        rows = self._query("SELECT * FROM pending_steps WHERE line_user_id=?", (user_id,))
        if not rows or rows[0]["kind"] != kind:
            return None
        if rows[0]["created_at"] < ts(since):
            self.clear_step(user_id)
            return None
        return rows[0]

    def clear_step(self, user_id: str) -> None:
        self._write("DELETE FROM pending_steps WHERE line_user_id=?", [(user_id,)])

    # ---- community reports ----
    def set_pending_report(self, user_id: str, level: str, now: datetime) -> None:
        self._set_step(user_id, "report", now, level)

    def pop_pending_report(self, user_id: str, since: datetime) -> str | None:
        """The level picked since `since`, consumed so one location makes one report."""
        row = self._step(user_id, "report", since)
        if row is None:
            return None
        self.clear_step(user_id)
        return row["level"]

    def add_report(
        self, reporter: str, level: str, location: GeoPoint, district: str, now: datetime, decimals: int
    ) -> None:
        self._write(
            "INSERT INTO community_reports (reporter, level, lat, lon, district_name, reported_at) "
            "VALUES (?,?,?,?,?,?)",
            [(reporter, level, round(location.lat, decimals), round(location.lon, decimals), district, ts(now))],
        )

    def report_count(self, reporter: str, since: datetime) -> int:
        q = "SELECT COUNT(*) FROM community_reports WHERE reporter=? AND reported_at>=?"
        return int(self._query(q, (reporter, ts(since)))[0][0])

    def live_reports(self, since: datetime) -> list[CommunityReport]:
        rows = self._query(
            "SELECT reporter, level, lat, lon, district_name, reported_at FROM community_reports "
            "WHERE reported_at>=? AND reporter IS NOT NULL",
            (ts(since),),
        )
        return [
            CommunityReport(
                reporter=r[0],
                level=r[1],
                location=GeoPoint(lat=r[2], lon=r[3]),
                district=r[4],
                reported_at=parse_ts(r[5]),
            )
            for r in rows
        ]

    def anonymize_reports(self, older_than: datetime) -> None:
        """Expired reports stay (for later verification studies) without who sent them; stale pending steps go."""
        self._write(
            "UPDATE community_reports SET reporter=NULL WHERE reported_at<? AND reporter IS NOT NULL",
            [(ts(older_than),)],
        )
        self._write("DELETE FROM pending_steps WHERE created_at<?", [(ts(older_than),)])

    # ---- route checks ----
    def start_route(self, user_id: str, now: datetime) -> None:
        """Waiting for the start point."""
        self._set_step(user_id, "route", now)

    def set_route_origin(self, user_id: str, origin: GeoPoint, now: datetime, decimals: int) -> None:
        self._write(
            "UPDATE pending_steps SET lat=?, lon=?, created_at=? WHERE line_user_id=? AND kind='route'",
            [(round(origin.lat, decimals), round(origin.lon, decimals), ts(now), user_id)],
        )

    def pending_route(self, user_id: str, since: datetime) -> PendingRoute | None:
        """The route check in progress, if its last step was since `since`."""
        row = self._step(user_id, "route", since)
        if row is None:
            return None
        return PendingRoute(origin=None if row["lat"] is None else GeoPoint(lat=row["lat"], lon=row["lon"]))

    def clear_pending_route(self, user_id: str) -> None:
        self._write("DELETE FROM pending_steps WHERE line_user_id=? AND kind='route'", [(user_id,)])

    # ---- PM2.5 lookups ----
    def start_air(self, user_id: str, now: datetime) -> None:
        self._set_step(user_id, "air", now)

    def air_pending(self, user_id: str, since: datetime) -> bool:
        """The user asked for PM2.5 since `since`. Not consumed: "check elsewhere" shares stay lookups."""
        return self._step(user_id, "air", since) is not None
