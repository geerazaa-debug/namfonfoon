"""FastAPI app: LINE webhook + B2B risk/routing API + rain poller + citizen report map + route check + PM2.5.

Subscribers, rain history and alert cooldowns persist in SQLite (`storage.db_path`, or env
FLOODWATCH_DB). One process only: the poller must not run in several workers at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import traceback
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from src.alerts import air_messages as am
from src.alerts import help_messages as hm
from src.alerts import report_messages as rm
from src.alerts import river_messages as rvm
from src.alerts import route_messages as rtm
from src.alerts.dispatcher import AlertPipeline, CooldownTracker, default_fetch
from src.alerts.line_login import InvalidIdToken, verify_id_token
from src.alerts.line_notifier import LineNotifier, verify_signature
from src.config import risk_thresholds, settings
from src.data.air4thai import AirStation, fetch_air
from src.data.bma_dds import fetch_floods as fetch_bma_floods
from src.data.bma_dds import fetch_rain as fetch_bma_rain
from src.data.db import Database
from src.data.osrm import fetch_route
from src.data.snapshot_cache import SnapshotCache
from src.data.thaiwater_level import RiverLevel, RiverStation, fetch_levels, river_levels
from src.data.thaiwater_rain import RainAccumulator
from src.data.validator import BoundingBox, GeoPoint
from src.engine.air import AirBand, air_bands, air_on_route, nearest_air
from src.engine.community import MapReport, ReportLevel, map_view, report_levels
from src.engine.districts import district_index
from src.engine.geofence import FloodZone, ZoneHit, haversine_km, zones_near_point, zones_on_route
from src.engine.risk_analyzer import AlertInput, Assessment, RuleBasedAnalyzer
from src.engine.route_check import (
    RainClass,
    RoadFlood,
    RouteCheck,
    StationRain,
    check_route,
    rain_class,
    rain_classes,
)

log = logging.getLogger(__name__)

analyzer = RuleBasedAnalyzer()
ZONES: dict[str, FloodZone] = {}
pipeline: AlertPipeline | None = None
_db: Database | None = None


def _minutes(section: str) -> timedelta:
    return timedelta(minutes=settings()[section]["cache_min"])


# One copy per source for all users. The lambdas look the fetchers up at call time (tests replace them).
air_cache: SnapshotCache[list[AirStation]] = SnapshotCache(
    "Air4Thai", lambda: fetch_air(), lambda: _minutes("air_quality")
)
bma_rain_cache: SnapshotCache[list[StationRain]] = SnapshotCache(
    "BMA rain", lambda: fetch_bma_rain(), lambda: _minutes("bma_dds")
)
river_cache: SnapshotCache[list[RiverStation]] = SnapshotCache(
    "ThaiWater levels", lambda: fetch_levels(), lambda: _minutes("river_level")
)
bma_flood_cache: SnapshotCache[list[RoadFlood]] = SnapshotCache(
    "BMA road floods", lambda: fetch_bma_floods(), lambda: _minutes("bma_dds")
)
ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"


def get_db() -> Database:
    """Open (and auto-create/migrate) the SQLite store on first use."""
    global _db
    if _db is None:
        path = os.environ.get("FLOODWATCH_DB") or str(ROOT / settings()["storage"]["db_path"])
        _db = Database(path)
    return _db


def public_url(path: str) -> str:
    """Absolute link for LINE replies; "" when PUBLIC_BASE_URL is unset (dev), so replies carry no link."""
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    return base + path if base else ""


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


app = FastAPI(title="น้ำ ฝน ฝุ่น (Nam Fon Foon) API", version="0.7.0", lifespan=lifespan)


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


def live_map_reports(now: datetime) -> list[MapReport]:
    cfg = settings()["community_reports"]
    ttl = timedelta(hours=cfg["ttl_hours"])
    return map_view(
        get_db().live_reports(now - ttl),
        now,
        confirm_radius_m=cfg["confirm_radius_m"],
        confirm_min_reporters=cfg["confirm_min_reporters"],
        ttl=ttl,
    )


@app.get("/v1/public/reports")
def public_reports() -> PublicReports:
    """Live citizen reports for the public map. No auth: contains no reporter identity."""
    now = datetime.now(UTC)
    levels = sorted(report_levels().values(), key=lambda lv: lv.rank)
    ttl_hours = settings()["community_reports"]["ttl_hours"]
    return PublicReports(generated_at=now, ttl_hours=ttl_hours, levels=levels, reports=live_map_reports(now))


class PublicRain(BaseModel):
    lat: float
    lon: float
    district: str | None
    rain_1h_mm: float
    rain: RainClass
    minutes_ago: int
    source: str  # attribution for the popup


class PublicRainView(BaseModel):
    generated_at: datetime
    classes: list[RainClass]  # map legend, lightest first
    stations: list[PublicRain]  # wet gauges only (rain >= lightest class)


@app.get("/v1/public/rain")
async def public_rain() -> PublicRainView:
    """Last hour's rain at wet gauges for the public map: BMA DDS (5-min) + ThaiWater (when the poller runs)."""
    now = datetime.now(UTC)
    classes = rain_classes()
    stations = [
        PublicRain(
            lat=s.location.lat,
            lon=s.location.lon,
            district=s.district,
            rain_1h_mm=s.rain_1h_mm,
            rain=cls,
            minutes_ago=int((now - s.observed_at).total_seconds() // 60),
            source="สำนักการระบายน้ำ กทม." if s.station_id.startswith("bma:") else "ThaiWater",
        )
        for s in await rain_stations(now)
        if (cls := rain_class(s.rain_1h_mm, classes))
    ]
    return PublicRainView(generated_at=now, classes=classes, stations=stations)


class PublicRoadFlood(BaseModel):
    lat: float
    lon: float
    name: str
    district: str | None
    depth_cm: float
    minutes_ago: int


class PublicRoadFloodView(BaseModel):
    generated_at: datetime
    sensors_online: int  # fresh road sensors, wet or dry
    min_flood_cm: float
    warn_flood_cm: float
    flooded: list[PublicRoadFlood]


@app.get("/v1/public/road-flood")
async def public_road_flood() -> PublicRoadFloodView:
    """BMA DDS road flood-depth sensors currently reporting water (display only, attributed)."""
    now = datetime.now(UTC)
    cfg = settings()["route_check"]
    sensors = await road_floods(now)
    flooded = [
        PublicRoadFlood(
            lat=f.location.lat,
            lon=f.location.lon,
            name=f.name,
            district=f.district,
            depth_cm=f.depth_cm,
            minutes_ago=int((now - f.observed_at).total_seconds() // 60),
        )
        for f in sensors
        if f.depth_cm >= cfg["min_flood_cm"]
    ]
    return PublicRoadFloodView(
        generated_at=now,
        sensors_online=len(sensors),
        min_flood_cm=cfg["min_flood_cm"],
        warn_flood_cm=cfg["warn_flood_cm"],
        flooded=sorted(flooded, key=lambda f: -f.depth_cm),
    )


async def air_stations(now: datetime) -> list[AirStation] | None:
    """Air4Thai snapshot shared by all users for `cache_min`; the last copy (or None) when a fetch fails."""
    return await air_cache.get(now)


async def rain_stations(now: datetime) -> list[StationRain]:
    """ThaiWater gauges (from the poller's store) + BMA DDS gauges, each within its own freshness limit."""
    thaiwater = get_db().latest_rain(now - timedelta(minutes=settings()["route_check"]["max_rain_age_min"]))
    bma_age = timedelta(minutes=settings()["bma_dds"]["max_age_min"])
    bma = [s for s in await bma_rain_cache.get(now) or [] if now - bma_age <= s.observed_at]
    return thaiwater + bma


async def road_floods(now: datetime) -> list[RoadFlood]:
    age = timedelta(minutes=settings()["bma_dds"]["max_age_min"])
    return [f for f in await bma_flood_cache.get(now) or [] if now - age <= f.observed_at]


async def river_stations(now: datetime) -> list[RiverStation] | None:
    """Fresh river/canal stations; None when ThaiWater cannot be reached and nothing is cached."""
    stations = await river_cache.get(now)
    if stations is None:
        return None
    age = timedelta(minutes=settings()["river_level"]["max_age_min"])
    return [s for s in stations if now - age <= s.observed_at]


class PublicRiverView(BaseModel):
    generated_at: datetime
    levels: list[RiverLevel]
    stations: list[RiverStation]


@app.get("/v1/public/river")
async def public_river() -> PublicRiverView:
    """River and canal levels around Bangkok (ThaiWater status, % of bank)."""
    now = datetime.now(UTC)
    return PublicRiverView(
        generated_at=now, levels=list(river_levels().values()), stations=await river_stations(now) or []
    )


async def river_reply(home: GeoPoint | None, now: datetime) -> list[dict[str, Any]]:
    cfg = settings()["river_level"]
    stations = await river_stations(now)
    if not stations:
        return [rvm.unavailable()]
    if home:
        ranked = sorted(((haversine_km(home, s.location), s) for s in stations), key=lambda x: x[0])[: cfg["nearest"]]
        picked = [(s, km) for km, s in ranked]
    else:
        by_code = {s.code: s for s in stations}
        picked = [(by_code[c], None) for c in cfg["key_stations"] if c in by_code]
    return [rvm.result(picked, river_levels(), near_home=home is not None, map_url=public_url("/map"), now=now)]


class PublicAir(BaseModel):
    lat: float
    lon: float
    name: str
    pm25: float
    band: int
    minutes_ago: int


class PublicAirView(BaseModel):
    generated_at: datetime
    bands: list[AirBand]  # map legend, cleanest first
    stations: list[PublicAir]


@app.get("/v1/public/air")
async def public_air() -> PublicAirView:
    """Latest PM2.5 at every fresh Air4Thai station (nationwide, includes BMA sensors)."""
    now = datetime.now(UTC)
    max_age = timedelta(minutes=settings()["air_quality"]["max_age_min"])
    stations = [
        PublicAir(
            lat=s.location.lat,
            lon=s.location.lon,
            name=s.name,
            pm25=s.pm25,
            band=s.band,
            minutes_ago=int((now - s.observed_at).total_seconds() // 60),
        )
        for s in await air_stations(now) or []
        if now - max_age <= s.observed_at
    ]
    return PublicAirView(generated_at=now, bands=list(air_bands().values()), stations=stations)


class AppConfig(BaseModel):
    name: str
    contact_email: str | None  # /privacy shows it when set
    privacy_updated: str
    liff_id: str | None
    report_url: str | None  # opens the LIFF report page inside LINE
    osrm_url: str  # /map draws a checked route itself, so route points never reach this server


@app.get("/v1/public/app-config")
def app_config() -> AppConfig:
    liff_id = os.environ.get("LIFF_ID") or None
    project = settings()["project"]
    return AppConfig(
        name=project["name"],
        contact_email=project["contact_email"] or None,
        privacy_updated=project["privacy_updated"],
        liff_id=liff_id,
        report_url=f"https://liff.line.me/{liff_id}" if liff_id else None,
        osrm_url=settings()["route_check"]["osrm_url"],
    )


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


@app.get("/privacy", include_in_schema=False)
def privacy_page() -> FileResponse:
    return _static("privacy.html", "text/html; charset=utf-8")


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


async def run_route_check(a: GeoPoint, b: GeoPoint, now: datetime) -> tuple[list[GeoPoint], RouteCheck, bool]:
    """(route, check, straight_line). Falls back to the straight line when OSRM fails or finds no road."""
    cfg = settings()["route_check"]
    try:
        path = await fetch_route(a, b)
    except (httpx.HTTPError, ValidationError) as exc:
        log.warning("route service failed: %s", type(exc).__name__)  # exception text carries coordinates
        path = None
    route = path.points if path else [a, b]
    check = check_route(
        route,
        await rain_stations(now),
        live_map_reports(now),
        now,
        station_buffer_km=cfg["station_buffer_km"],
        report_buffer_m=cfg["report_buffer_m"],
        max_rain_age=timedelta(minutes=cfg["max_rain_age_min"]),
        warn_rain_mm=cfg["warn_rain_mm"],
        warn_report_rank=cfg["warn_report_rank"],
        levels=report_levels(),
        district_of=district_index().locate,
        road_floods=await road_floods(now),
        flood_buffer_m=cfg["flood_buffer_m"],
        min_flood_cm=cfg["min_flood_cm"],
        warn_flood_cm=cfg["warn_flood_cm"],
        max_flood_age=timedelta(minutes=settings()["bma_dds"]["max_age_min"]),
    )
    log.info("route check verdict=%s", check.verdict)  # never log points
    return route, check, path is None


async def route_step(user_id: str, loc: GeoPoint, now: datetime) -> list[dict[str, Any]] | None:
    """Location share during a route check; None when no route check is in progress."""
    cfg = settings()["route_check"]
    db = get_db()
    pending = db.pending_route(user_id, now - timedelta(minutes=cfg["pending_ttl_min"]))
    if pending is None:
        return None
    aoi = BoundingBox(**{k: v for k, v in settings()["area_of_interest"].items() if k != "name"})
    if not aoi.contains(loc):
        db.clear_pending_route(user_id)
        return [rtm.outside_area()]
    if pending.origin is None:
        db.set_route_origin(user_id, loc, now, cfg["coord_decimals"])
        return [rtm.ask_destination()]
    db.clear_pending_route(user_id)
    d = cfg["coord_decimals"]  # OSRM sees both ends as coarsely as the stored start point
    loc = GeoPoint(lat=round(loc.lat, d), lon=round(loc.lon, d))
    route, check, straight = await run_route_check(pending.origin, loc, now)
    air_cfg = settings()["air_quality"]
    air = air_on_route(
        route,
        await air_stations(now) or [],
        now,
        buffer_km=air_cfg["route_buffer_km"],
        max_age=timedelta(minutes=air_cfg["max_age_min"]),
    )
    air_line = am.route_line(air, air_bands()[air.worst.band]) if air else None
    map_url = public_url("/map")
    return [rtm.result(check, straight_line=straight, map_url=map_url, a=pending.origin, b=loc, air_line=air_line)]


async def air_reply(loc: GeoPoint, now: datetime, *, at_home: bool) -> list[dict[str, Any]]:
    cfg = settings()["air_quality"]
    stations = await air_stations(now)
    if stations is None:
        return [am.unavailable()]
    near = nearest_air(loc, stations, now, max_km=cfg["nearest_max_km"], max_age=timedelta(minutes=cfg["max_age_min"]))
    if near is None:
        return [am.none_nearby(cfg["nearest_max_km"])]
    log.info("air lookup band=%d", near.station.band)  # never log points
    return [am.result(near, air_bands()[near.station.band], cfg["sensitive_group"], at_home=at_home)]


async def handle_event(ev: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Apply one webhook event; returns the reply messages (possibly none)."""
    cfg = settings()["community_reports"]
    route_cfg = settings()["route_check"]
    air_cfg = settings()["air_quality"]
    project = settings()["project"]
    levels = report_levels()
    user_id = ev.get("source", {}).get("userId")
    if not user_id:
        return []
    db = get_db()
    kind, msg = ev.get("type"), ev.get("message", {})
    if kind == "unfollow":
        db.delete_user(user_id)  # also cooldowns + pending step; anonymizes past reports
        return []
    if kind == "follow":  # replaces the console greeting message: turn that off
        return [hm.welcome(project["name"], public_url("/privacy"))]
    text = msg.get("text", "").strip() if kind == "message" and msg.get("type") == "text" else None
    data = ev.get("postback", {}).get("data", "") if kind == "postback" else None
    if text in project["help_texts"] or data == hm.POSTBACK_HELP:
        return [hm.help_reply(project["name"], public_url("/privacy"))]
    if text in project["my_data_texts"]:
        return [hm.my_data(db.user_data(user_id, now - timedelta(hours=cfg["ttl_hours"])))]
    if text in project["delete_texts"]:
        return [hm.confirm_delete()]
    if data == hm.POSTBACK_DELETE:
        db.delete_user(user_id)
        log.info("user deleted their data")  # never log userId
        return [hm.deleted()]
    if data == hm.POSTBACK_CANCEL:
        return [hm.cancelled()]
    if data == hm.POSTBACK_HOME:
        db.clear_step(user_id)  # so the next shared location is the home location
        return [hm.ask_home()]
    if text in cfg["trigger_texts"]:
        return [rm.ask_level(levels)]
    if text in route_cfg["trigger_texts"] or data == rtm.POSTBACK_START:
        db.anonymize_reports(now - timedelta(hours=cfg["ttl_hours"]))  # also drops stale pending steps
        db.start_route(user_id, now)
        return [rtm.ask_origin(route_cfg["pending_ttl_min"])]
    air_text = text is not None and text.lower().replace(" ", "") in air_cfg["trigger_texts"]
    if air_text or data == am.POSTBACK_START:
        db.start_air(user_id, now)  # "check elsewhere" button shares a location next
        home = db.user_location(user_id)
        return await air_reply(home, now, at_home=True) if home else [am.ask_location(air_cfg["pending_ttl_min"])]
    if text in settings()["river_level"]["trigger_texts"] or data == rvm.POSTBACK_START:
        return await river_reply(db.user_location(user_id), now)
    if data is not None:
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
        if level is None and (route_reply := await route_step(user_id, loc, now)) is not None:
            return route_reply
        if level is None and db.air_pending(user_id, now - timedelta(minutes=air_cfg["pending_ttl_min"])):
            db.start_air(user_id, now)
            return await air_reply(loc, now, at_home=False)
        if level is None:  # plain location share = alert subscription (home area)
            district = district_name(loc)
            db.upsert_user(user_id, loc, district, now)
            log.info("registered location for subscriber")  # never log userId/coords
            return [hm.home_saved(district)]
        outcome = submit_report(user_id, level, loc, now)
        if outcome.status == "outside_bangkok":
            return [rm.outside_bangkok()]
        if outcome.status == "rate_limited":
            return [rm.too_many(cfg["max_reports_per_hour"])]
        return [rm.thanks(levels[level], outcome.district or "", public_url("/map"))]
    return []


async def process_events(events: list[dict[str, Any]], now: datetime) -> None:
    for ev in events:
        if ev.get("deliveryContext", {}).get("isRedelivery"):
            continue  # already handled; replaying would e.g. turn a consumed report location into a subscription
        try:
            replies = await handle_event(ev, now)
        except Exception as exc:  # stack without the message: validation errors echo input coordinates
            stack = "".join(traceback.format_tb(exc.__traceback__))
            log.error("webhook event failed: %s\n%s", type(exc).__name__, stack)
            continue
        if replies and ev.get("replyToken"):
            await send_reply(ev["replyToken"], replies)


@app.post("/webhook/line")
async def line_webhook(
    request: Request, background: BackgroundTasks, x_line_signature: str = Header(default="")
) -> dict[str, str]:
    """Acknowledge at once; events (a route check calls OSRM) run after the response is sent."""
    secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    body = await request.body()
    if not secret or not verify_signature(body, x_line_signature, secret):
        raise HTTPException(status_code=401, detail="bad signature")
    background.add_task(process_events, json.loads(body).get("events", []), datetime.now(UTC))
    return {"status": "ok"}
