"""FastAPI app: LINE webhook + B2B risk/routing API + background rain poller.

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

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.alerts.dispatcher import AlertPipeline, CooldownTracker, default_fetch
from src.alerts.line_notifier import LineNotifier, verify_signature
from src.config import risk_thresholds, settings
from src.data.db import Database
from src.data.thaiwater_rain import RainAccumulator
from src.data.validator import GeoPoint
from src.engine.districts import district_index
from src.engine.geofence import FloodZone, ZoneHit, zones_near_point, zones_on_route
from src.engine.risk_analyzer import AlertInput, Assessment, RuleBasedAnalyzer

log = logging.getLogger(__name__)

analyzer = RuleBasedAnalyzer()
ZONES: dict[str, FloodZone] = {}
pipeline: AlertPipeline | None = None
_db: Database | None = None
ROOT = Path(__file__).resolve().parents[2]


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


app = FastAPI(title="FloodWatch Rain Advisory", version="0.2.0", lifespan=lifespan)


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


@app.post("/webhook/line")
async def line_webhook(request: Request, x_line_signature: str = Header(default="")) -> dict[str, str]:
    secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    body = await request.body()
    if not secret or not verify_signature(body, x_line_signature, secret):
        raise HTTPException(status_code=401, detail="bad signature")
    for ev in json.loads(body).get("events", []):
        user_id = ev.get("source", {}).get("userId")
        msg = ev.get("message", {})
        if ev.get("type") == "message" and msg.get("type") == "location" and user_id:
            loc = GeoPoint(lat=msg["latitude"], lon=msg["longitude"])
            get_db().upsert_user(user_id, loc, district_name(loc), datetime.now(UTC))
            log.info("registered location for subscriber")  # never log userId/coords
        elif ev.get("type") == "unfollow" and user_id:
            get_db().delete_user(user_id)  # also deletes the user's cooldown rows
    return {"status": "ok"}
