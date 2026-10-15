import pytest

from src.api import server
from src.data.db import Database


@pytest.fixture(autouse=True)
def memory_db(monkeypatch):
    """Every test gets a fresh in-memory SQLite store; no file is ever created."""
    db = Database(":memory:")
    monkeypatch.setattr(server, "_db", db)
    yield db
    db.close()


@pytest.fixture(autouse=True)
def air_feed(monkeypatch):
    """No network: Air4Thai returns this list (empty unless a test fills it); the cache starts empty."""
    stations: list = []

    async def fake_fetch_air() -> list:
        return list(stations)

    server.air_cache.reset()
    monkeypatch.setattr(server, "fetch_air", fake_fetch_air)
    return stations


@pytest.fixture(autouse=True)
def bma_feed(monkeypatch):
    """No network: BMA DDS returns these lists (empty unless a test fills them)."""
    feed: dict[str, list] = {"rain": [], "floods": []}

    async def fake_rain() -> list:
        return list(feed["rain"])

    async def fake_floods() -> list:
        return list(feed["floods"])

    server.bma_rain_cache.reset()
    server.bma_flood_cache.reset()
    monkeypatch.setattr(server, "fetch_bma_rain", fake_rain)
    monkeypatch.setattr(server, "fetch_bma_floods", fake_floods)
    return feed
