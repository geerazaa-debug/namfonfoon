"""FastAPI app: LINE webhook + B2B risk/routing API + background rain poller + citizen report map.

Subscribers, rain history and alert cooldowns persist in SQLite (`storage.db_path`, or env
FLOODWATCH_DB). One process only: the poller must not run in several workers at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.alerts import report_messages as rm
from src.alerts.dispatcher import AlertPipeline, CooldownTracker, default_fetch
from src.alerts.line_login import InvalidIdToken, verify_id_token
from src.alerts.line_notifier import LineNotifier, verify_signature
from src.config import risk_thresholds, settings
from src.data.db import Database
from src.data.thaiwater_rain import RainAccumulator
from src.data.validator import GeoPoint
from src.engine.community import MapReport, ReportLevel, map_view, report_levels
from src.engine.districts import district_index
from src.engine.geofence import FloodZone, ZoneHit, zones_near_point, zones_on_route
from src.engine.risk_analyzer import AlertInput, Assessment, RuleBasedAnalyzer

log = logging.getLogger(__name__)

analyzer = RuleBasedAnalyzer()
ZONES: dict[str, FloodZone] = {}
pipeline: AlertPipeline | None = None
_db: Database | None = None
ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"


def get_db() -> Database:
    """Open (and auto-create/migrate) the SQLite store on first use."""
    global _db
    if _db is None:
        path = os.environ.get("FLOODWATCH_DB") or str(ROOT / settings()["storage"]["db_path"])
        _db = Database(path)
    return _db


def district_name(p: GeoPoint) -> str | None:
    d = district_index().locate(p)
    return d.th if d else None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the rain poller when POLLER_ENABLED=1. Without a LINE token it runs dry (assess, never push)."""
    global pipeline
    if os.environ.get("POLLER_ENABLED") != "1":
        yield
        return
    async with AsyncExitStack() as stack:
        send = None
        if os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"):
            send = (await stack.enter_async_context(LineNotifier())).push
        else:
            log.warning("LINE_CHANNEL_ACCESS_TOKEN not set: rain poller runs in dry mode")
        db = get_db()
        pipeline = AlertPipeline(
            fetch=default_fetch(),
            subscribers=db.users,
            send=send,
            analyzer=analyzer,
            accumulator=RainAccumulator(db=db, district_of=district_name),
            cooldown=CooldownTracker(timedelta(minutes=risk_thresholds()["alerts"]["cooldown_min"]), db=db),
        )
        stop = asyncio.Event()
        task = asyncio.create_task(pipeline.run_forever(settings()["rain_pipeline"]["poll_interval_min"] * 60, stop))
        try:
            yield
        finally:
            stop.set()
            await task
            pipeline = None


app = FastAPI(title="FloodWatch Rain Advisory", version="0.3.0", lifespan=lifespan)


def require_api_key(x_api_key: str = Header(default="")) -> None:
    keys = [k for k in os.environ.get("B2B_API_KEYS", "").split(",") if k]
    if not keys or not any(secrets.compare_digest(x_api_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="invalid API key")


class PointQuery(BaseModel):
    location: GeoPoint
    radius_km: float = Field(default_factory=lambda: settings()["geofence"]["default_radius_km"], ge=0, le=50)


class RouteQuery(BaseModel):
    route: list[GeoPoint] = Field(min_length=1, max_length=5000)
    buffer_km: float = Field(default=0.5, ge=0, le=10)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/risk/assess", dependencies=[Depends(require_api_key)])
def assess(inp: AlertInput) -> Assessment:
    return analyzer.assess(inp)


@app.post("/v1/zones/nearby", dependencies=[Depends(require_api_key)])
def zones_nearby(q: PointQuery) -> list[ZoneHit]:
    return zones_near_point(q.location, ZONES.values(), q.radius_km)


@app.post("/v1/route/check", dependencies=[Depends(require_api_key)])
def route_check(q: RouteQuery) -> dict[str, object]:
    hits = zones_on_route(q.route, ZONES.values(), q.buffer_km)
    return {"safe": not hits, "hits": hits}


class ReportOutcome(BaseModel):
    status: Literal["stored", "outside_bangkok", "rate_limited"]
    district: str | None = None


def submit_report(user_id: str, level: str, loc: GeoPoint, now: datetime) -> ReportOutcome:
    """Shared by the LINE chat flow and the LIFF page."""
    cfg = settings()["community_reports"]
    db = get_db()
    district = district_name(loc)
    if district is None:
        return ReportOutcome(status="outside_bangkok")
    if db.report_count(user_id, now - timedelta(hours=1)) >= cfg["max_reports_per_hour"]:
        return ReportOutcome(status="rate_limited", district=district)
    db.anonymize_reports(now - timedelta(hours=cfg["ttl_hours"]))
    db.add_report(user_id, level, loc, district, now, cfg["coord_decimals"])
    log.info("stored community report level=%s", level)  # never log userId/coords
    return ReportOutcome(status="stored", district=district)


class PublicReports(BaseModel):
    generated_at: datetime
    ttl_hours: float
    levels: list[ReportLevel]  # map legend, highest rank last
    reports: list[MapReport]


@app.get("/v1/public/reports")
def public_reports() -> PublicReports:
    """Live citizen reports for the public map. No auth: contains no reporter identity."""
    cfg = settings()["community_reports"]
    now = datetime.now(UTC)
    ttl = timedelta(hours=cfg["ttl_hours"])
    reports = map_view(
        get_db().live_reports(now - ttl),
        now,
        confirm_radius_m=cfg["confirm_radius_m"],
        confirm_min_reporters=cfg["confirm_min_reporters"],
        ttl=ttl,
    )
    levels = sorted(report_levels().values(), key=lambda lv: lv.rank)
    return PublicReports(generated_at=now, ttl_hours=cfg["ttl_hours"], levels=levels, reports=reports)


class AppConfig(BaseModel):
    liff_id: str | None
    report_url: str | None  # opens the LIFF report page inside LINE


@app.get("/v1/public/app-config")
def app_config() -> AppConfig:
    liff_id = os.environ.get("LIFF_ID") or None
    return AppConfig(liff_id=liff_id, report_url=f"https://liff.line.me/{liff_id}" if liff_id else None)


class ReportRequest(BaseModel):
    level: str
    location: GeoPoint


@app.post("/v1/reports")
async def create_report(req: ReportRequest, authorization: str = Header(default="")) -> ReportOutcome:
    """LIFF page submit. Auth = LINE Login ID token, so identity and rate limits match the chat flow."""
    channel_id = os.environ.get("LINE_LOGIN_CHANNEL_ID", "")
    if not channel_id:
        raise HTTPException(status_code=503, detail="web reporting not configured")
    if req.level not in report_levels():
        raise HTTPException(status_code=422, detail="unknown level")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing ID token")
    try:
        claims = await verify_id_token(token, channel_id, settings()["community_reports"]["id_token_verify_url"])
    except InvalidIdToken:
        raise HTTPException(status_code=401, detail="invalid ID token") from None
    outcome = submit_report(claims.sub, req.level, req.location, datetime.now(UTC))
    if outcome.status == "rate_limited":
        raise HTTPException(status_code=429, detail="too many reports")
    return outcome


def _static(name: str, media_type: str) -> FileResponse:
    return FileResponse(STATIC / name, media_type=media_type)


@app.get("/map", include_in_schema=False)
def report_map() -> FileResponse:
    return _static("map.html", "text/html; charset=utf-8")


@app.get("/report", include_in_schema=False)
def report_page() -> FileResponse:
    return _static("report.html", "text/html; charset=utf-8")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return _static("manifest.webmanifest", "application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    """Served from the root so the worker's scope covers /map and the API."""
    return _static("sw.js", "text/javascript")


@app.get("/icons/{name}", include_in_schema=False)
def icon(name: Literal["icon-192.png", "icon-512.png"]) -> FileResponse:
    return _static(name, "image/png")


async def send_reply(reply_token: str, messages: list[dict[str, Any]]) -> None:
    """Best effort: without a token (dev) or on LINE errors, the report is still stored."""
    if not os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"):
        log.warning("LINE_CHANNEL_ACCESS_TOKEN not set: reply skipped")
        return
    try:
        async with LineNotifier() as line:
            await line.reply(reply_token, messages, settings()["community_reports"]["reply_url"])
    except Exception:
        log.exception("LINE reply failed")


def handle_event(ev: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Apply one webhook event; returns the reply messages (possibly none)."""
    cfg = settings()["community_reports"]
    levels = report_levels()
    user_id = ev.get("source", {}).get("userId")
    if not user_id:
        return []
    db = get_db()
    kind, msg = ev.get("type"), ev.get("message", {})
    if kind == "unfollow":
        db.delete_user(user_id)  # also cooldowns + pending report; anonymizes past reports
        return []
    if kind == "message" and msg.get("type") == "text" and msg.get("text", "").strip() in cfg["trigger_texts"]:
        return [rm.ask_level(levels)]
    if kind == "postback":
        data = ev.get("postback", {}).get("data", "")
        key = data.removeprefix(rm.POSTBACK_PREFIX) if data.startswith(rm.POSTBACK_PREFIX) else None
        if key == "start":  # rich-menu button
            return [rm.ask_level(levels)]
        if key in levels:
            db.set_pending_report(user_id, key, now)
            return [rm.ask_location(levels[key], cfg["pending_ttl_min"])]
        return []
    if kind == "message" and msg.get("type") == "location":
        loc = GeoPoint(lat=msg["latitude"], lon=msg["longitude"])
        level = db.pop_pending_report(user_id, now - timedelta(minutes=cfg["pending_ttl_min"]))
        if level is None:  # plain location share = alert subscription (home area)
            db.upsert_user(user_id, loc, district_name(loc), now)
            log.info("registered location for subscriber")  # never log userId/coords
            return []
        outcome = submit_report(user_id, level, loc, now)
        if outcome.status == "outside_bangkok":
            return [rm.outside_bangkok()]
        if outcome.status == "rate_limited":
            return [rm.too_many(cfg["max_reports_per_hour"])]
        return [rm.thanks(levels[level], outcome.district or "", cfg["public_map_url"])]
    return []


@app.post("/webhook/line")
async def line_webhook(request: Request, x_line_signature: str = Header(default="")) -> dict[str, str]:
    secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    body = await request.body()
    if not secret or not verify_signature(body, x_line_signature, secret):
        raise HTTPException(status_code=401, detail="bad signature")
    now = datetime.now(UTC)
    for ev in json.loads(body).get("events", []):
        if ev.get("deliveryContext", {}).get("isRedelivery"):
            continue  # already handled; replaying would e.g. turn a consumed report location into a subscription
        replies = handle_event(ev, now)
        if replies and ev.get("replyToken"):
            await send_reply(ev["replyToken"], replies)
    return {"status": "ok"}
