import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from src.data.nasa_client import NasaClient
from src.data.validator import BoundingBox

CFG = {
    "eonet_url": "https://eonet.gsfc.nasa.gov/api/v3/events",
    "cmr_granules_url": "https://cmr.earthdata.nasa.gov/search/granules.json",
    "flood_collection_short_name": "MCDWD_L3_NRT",
    "timeout_s": 5,
    "max_retries": 2,
    "backoff_base_s": 0.0,
}
BBOX = BoundingBox(min_lat=13.4, max_lat=14.1, min_lon=100.25, max_lon=100.95)

EONET = {
    "events": [
        {
            "id": "EONET_1",
            "title": "Floods - Thailand",
            "closed": None,
            "sources": [{"id": "GDACS", "url": "https://example.org/1"}],
            "geometry": [
                {"date": "2026-10-01T00:00:00Z", "type": "Point", "coordinates": [100.5, 13.7]},
                {"date": "2026-10-03T00:00:00Z", "type": "Point", "coordinates": [100.6, 13.8]},
            ],
        },
        {"id": "EONET_2", "title": "polygon only", "geometry": [{"date": "2026-10-01T00:00:00Z", "type": "Polygon"}]},
    ]
}


def run(coro):
    return asyncio.run(coro)


def test_flood_events_parses_latest_point_and_skips_polygons():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=EONET)

    async def go():
        async with NasaClient(api_key="SECRET", cfg=CFG, transport=httpx.MockTransport(handler)) as c:
            return await c.flood_events(BBOX)

    events = run(go())
    assert len(events) == 1
    assert events[0].location.lat == 13.8 and events[0].location.lon == 100.6
    assert seen[0].url.params["category"] == "floods"
    assert seen[0].url.params["bbox"] == "100.25,14.1,100.95,13.4"
    assert "api_key" not in seen[0].url.params  # key only sent to api.nasa.gov


def test_api_key_attached_for_api_nasa_gov():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"events": []})

    cfg = {**CFG, "eonet_url": "https://api.nasa.gov/eonet/events"}

    async def go():
        async with NasaClient(api_key="K", cfg=cfg, transport=httpx.MockTransport(handler)) as c:
            await c.flood_events(BBOX)

    run(go())
    assert seen[0].url.params["api_key"] == "K"


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"events": []})

    async def go():
        async with NasaClient(api_key="", cfg=CFG, transport=httpx.MockTransport(handler)) as c:
            return await c.flood_events(BBOX)

    assert run(go()) == []
    assert calls["n"] == 3


def test_gives_up_after_max_retries():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def go():
        async with NasaClient(api_key="", cfg=CFG, transport=httpx.MockTransport(handler)) as c:
            await c.flood_events(BBOX)

    with pytest.raises(httpx.HTTPStatusError):
        run(go())


def test_no_retry_on_404():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    async def go():
        async with NasaClient(api_key="", cfg=CFG, transport=httpx.MockTransport(handler)) as c:
            await c.flood_events(BBOX)

    with pytest.raises(httpx.HTTPStatusError):
        run(go())
    assert calls["n"] == 1


def test_flood_granules_query_and_parse():
    seen: list[httpx.Request] = []
    feed = {
        "feed": {
            "entry": [
                {
                    "id": "G1",
                    "title": "MCDWD tile",
                    "time_start": "2026-10-05T00:00:00Z",
                    "time_end": "2026-10-05T23:59:59Z",
                    "links": [{"href": "https://x/g1.tif"}],
                }
            ]
        }
    }

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=feed)

    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 7, tzinfo=UTC)

    async def go():
        async with NasaClient(api_key="", cfg=CFG, transport=httpx.MockTransport(handler)) as c:
            return await c.flood_granules(BBOX, start, end)

    granules = run(go())
    assert granules[0].id == "G1" and granules[0].links == ["https://x/g1.tif"]
    p = seen[0].url.params
    assert p["short_name"] == "MCDWD_L3_NRT"
    assert p["bounding_box"] == "100.25,13.4,100.95,14.1"
