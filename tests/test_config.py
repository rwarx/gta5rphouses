"""Tests for configuration parsing and defaults."""

import importlib

from app.config import settings as settings_module
from app.config.settings import (
    DatabaseSettings,
    SmartModeSettings,
    TelegramSettings,
    get_settings,
)


def test_get_settings_is_singleton():
    assert get_settings() is get_settings()


def test_telegram_allowed_users_parsing():
    t = TelegramSettings()
    t.allowed_user_ids = "123, 456 ,789"
    assert t.allowed_users == [123, 456, 789]


def test_telegram_allowed_users_empty():
    t = TelegramSettings()
    t.allowed_user_ids = ""
    assert t.allowed_users == []


def test_database_url_normalizes_railway_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host:5432/db")
    # Reload module so env() picks up the new value for the class attribute.
    importlib.reload(settings_module)
    db = settings_module.DatabaseSettings()
    assert db.database_url.startswith("postgresql+asyncpg://")
    # Sync URL is derived without the asyncpg driver.
    assert db.database_sync_url.startswith("postgresql://")
    assert "+asyncpg" not in db.database_sync_url


def test_smart_mode_validation_rejects_bad_interval(monkeypatch):
    monkeypatch.setenv("LOW_INTERVAL", "0")
    importlib.reload(settings_module)
    try:
        settings_module.SmartModeSettings()
        assert False, "expected AssertionError for LOW_INTERVAL=0"
    except AssertionError:
        pass
    finally:
        monkeypatch.delenv("LOW_INTERVAL", raising=False)
        importlib.reload(settings_module)
