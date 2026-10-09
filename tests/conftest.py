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
