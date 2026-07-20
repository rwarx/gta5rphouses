"""Tests for configuration parsing and defaults."""

import importlib

import pytest

from app.config import settings as settings_module
from app.config.settings import (
    DatabaseSettings,
    RealEstateSettings,
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


def test_realestate_server_names_falls_back_to_primary():
    rs = RealEstateSettings()
    rs.server_name = "Murrieta"
    rs._servers_raw = ""
    assert rs.server_names == ["Murrieta"]


def test_realestate_server_names_includes_primary_first_and_dedupes():
    rs = RealEstateSettings()
    rs.server_name = "Murrieta"
    rs._servers_raw = "Strawberry, murrieta ,Sunrise"
    # Primary always first; case-insensitive duplicate of primary dropped.
    assert rs.server_names == ["Murrieta", "Strawberry", "Sunrise"]


def test_smart_mode_validation_rejects_bad_interval(monkeypatch):
    monkeypatch.setenv("LOW_INTERVAL", "0")
    importlib.reload(settings_module)
    try:
        # Validation raises ValueError (not assert) so it survives `python -O`.
        with pytest.raises(ValueError):
            settings_module.SmartModeSettings()
    finally:
        monkeypatch.delenv("LOW_INTERVAL", raising=False)
        importlib.reload(settings_module)
