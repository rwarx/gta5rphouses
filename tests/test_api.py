"""Smoke tests for the FastAPI endpoints using a temporary SQLite database."""

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Use a file-based SQLite DB so the app's own init_db() (which runs in the
    # lifespan handler) creates the schema for us.
    db_file = tmp_path / "api_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")

    # Reset the settings singleton and the DatabaseSession so they pick up the
    # per-test database URL.
    from app.config import settings as settings_module
    settings_module._settings = None
    from app.database.session import DatabaseSession
    DatabaseSession._engine = None
    DatabaseSession._session_factory = None

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_apartments_empty(client):
    r = client.get("/api/v1/apartments")
    assert r.status_code == 200
    assert r.json() == []


def test_statistics_shape(client):
    r = client.get("/api/v1/statistics")
    assert r.status_code == 200
    body = r.json()
    assert "apartments" in body
    assert "scraper" in body
    assert "total_apartments" in body["apartments"]
    assert "total_free" in body["apartments"]


def test_apartment_not_found(client):
    r = client.get("/api/v1/apartments/9999")
    assert r.status_code == 404


def test_changes_empty(client):
    r = client.get("/api/v1/changes")
    assert r.status_code == 200
    assert r.json() == []
