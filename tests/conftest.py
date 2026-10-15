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

    monkeypatch.setattr(server, "_air_cache", None)
    monkeypatch.setattr(server, "fetch_air", fake_fetch_air)
    return stations
