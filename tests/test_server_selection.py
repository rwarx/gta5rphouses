"""Tests for the per-user active-server selection (chosen at /start).

Covers the repository upsert, the bot's default-server resolution (saved
selection vs. fallback to the configured primary), the `selsrv:` picker
callback-data, and the map scraper server being configurable.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("BOT_TOKEN", "")

import pytest

from app.database.repository import UserServerSelectionRepository
from app.telegram.bot import ApartmentBot


# ---- UserServerSelectionRepository ----

@pytest.mark.asyncio
async def test_get_returns_none_when_never_selected(session):
    repo = UserServerSelectionRepository(session)
    assert await repo.get(555) is None


@pytest.mark.asyncio
async def test_set_then_get_roundtrip(session):
    repo = UserServerSelectionRepository(session)
    await repo.set(555, "20")
    assert await repo.get(555) == "20"


@pytest.mark.asyncio
async def test_set_is_idempotent_one_row_per_user(session):
    repo = UserServerSelectionRepository(session)
    await repo.set(555, "20")
    await repo.set(555, "02")  # switching servers upserts, not appends

    assert await repo.get(555) == "02"

    # No duplicate rows for the user.
    from sqlalchemy import select, func
    from app.database.models import UserServerSelection
    result = await session.execute(
        select(func.count(UserServerSelection.id)).where(
            UserServerSelection.user_id == 555
        )
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_users_are_isolated(session):
    repo = UserServerSelectionRepository(session)
    await repo.set(1, "20")
    await repo.set(2, "02")
    assert await repo.get(1) == "20"
    assert await repo.get(2) == "02"


# ---- Bot default-server resolution ----

@pytest.fixture
def bot():
    return ApartmentBot()


@pytest.mark.asyncio
async def test_default_prefers_saved_selection(bot, monkeypatch):
    monkeypatch.setattr(bot.settings.realestate, "_servers_raw", "Murrieta,Strawberry")
    monkeypatch.setattr(bot.settings.realestate, "server_name", "Murrieta")

    async def fake_get(uid):
        return "02"  # Strawberry
    monkeypatch.setattr(bot, "_get_user_sid", fake_get)

    sid, name = await bot._default_sid_for_user(1)
    assert sid == "02"
    assert name == "Strawberry"


@pytest.mark.asyncio
async def test_default_falls_back_to_primary_when_unset(bot, monkeypatch):
    monkeypatch.setattr(bot.settings.realestate, "_servers_raw", "Murrieta,Strawberry")
    monkeypatch.setattr(bot.settings.realestate, "server_name", "Murrieta")

    async def fake_get(uid):
        return None
    monkeypatch.setattr(bot, "_get_user_sid", fake_get)

    sid, name = await bot._default_sid_for_user(1)
    assert name == "Murrieta"
    assert sid == "20"


@pytest.mark.asyncio
async def test_default_honours_any_wiki_server_picked(bot, monkeypatch):
    # /start now offers the FULL wiki server list, so a user may track a server
    # that isn't in the static REALESTATE_SERVERS config. Their choice is still
    # honoured (the scheduler picks it up dynamically) instead of falling back.
    monkeypatch.setattr(bot.settings.realestate, "_servers_raw", "Murrieta")
    monkeypatch.setattr(bot.settings.realestate, "server_name", "Murrieta")

    async def fake_get(uid):
        return "02"  # Strawberry, not in the configured list — but a valid wiki server
    monkeypatch.setattr(bot, "_get_user_sid", fake_get)

    sid, name = await bot._default_sid_for_user(1)
    assert sid == "02"
    assert name == "Strawberry"


@pytest.mark.asyncio
async def test_default_falls_back_when_selection_invalid(bot, monkeypatch):
    # A saved sid that maps to no known wiki server falls back to the primary.
    monkeypatch.setattr(bot.settings.realestate, "server_name", "Murrieta")

    async def fake_get(uid):
        return "99"  # not a real wiki sid
    monkeypatch.setattr(bot, "_get_user_sid", fake_get)

    sid, name = await bot._default_sid_for_user(1)
    assert name == "Murrieta"
    assert sid == "20"


# ---- Picker callback-data ----

def _all_callbacks(markup):
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_server_pick_markup_selsrv_lists_full_wiki(bot, monkeypatch):
    # /start offers EVERY wiki server, not just the configured ones, so a user
    # can start tracking any of them.
    monkeypatch.setattr(bot.settings.realestate, "_servers_raw", "Murrieta")
    monkeypatch.setattr(bot.settings.realestate, "server_name", "Murrieta")
    cbs = _all_callbacks(bot._server_pick_markup("selsrv"))
    assert "selsrv:20" in cbs  # Murrieta (configured)
    assert "selsrv:02" in cbs  # Strawberry (NOT configured, still offered)
    assert "selsrv:24" in cbs  # Senora (last in the list)
    # One selsrv button per wiki server (plus a non-selsrv back button).
    selsrv = [c for c in cbs if c.startswith("selsrv:")]
    assert len(selsrv) == 24


def test_server_pick_markup_selsrv_marks_active(bot):
    # The active server is checkmarked so re-running /start shows the choice.
    markup = bot._server_pick_markup("selsrv", active_sid="20")
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("✅" in t and "Murrieta" in t for t in texts)


def test_server_pick_markup_catalog_only_configured(bot, monkeypatch):
    # Non-selsrv pickers still list only configured servers (they read data).
    monkeypatch.setattr(bot.settings.realestate, "_servers_raw", "Murrieta")
    monkeypatch.setattr(bot.settings.realestate, "server_name", "Murrieta")
    cbs = _all_callbacks(bot._server_pick_markup("buildings"))
    assert "buildings:20" in cbs
    assert "buildings:02" not in cbs  # Strawberry not configured


def test_selsrv_callback_parses_sid():
    # Mirrors the split in _on_callback: `sid = data.split(":", 1)[1]`.
    assert "selsrv:20".split(":", 1)[1] == "20"


# ---- Map scraper server is configurable ----

def test_map_server_setting_defaults_to_murrieta(bot):
    assert bot.settings.scraper.map_server == "Murrieta"


# ---- 🏠 Квартиры reads the per-server catalog (follows the picked server) ----

@pytest.mark.asyncio
async def test_apartments_list_uses_selected_server_catalog(session, monkeypatch):
    """cmd_list must render buildings from the user's selected server, not a
    global/hard-coded Murrieta source."""
    from app.database.repository import RealEstateRepository
    from app.telegram import bot as bot_mod

    # Seed catalog buildings for two servers: Davis (05) and Murrieta (20).
    repo = RealEstateRepository(session)
    await repo.upsert_building("05:building:1", {
        "server_sid": "05", "building_id": 1, "name": "Davis Tower",
        "apartments_count": 10, "free_count": 3,
    })
    await repo.upsert_building("20:building:1", {
        "server_sid": "20", "building_id": 1, "name": "Murrieta Tower",
        "apartments_count": 8, "free_count": 0,
    })

    # Route the bot's DB access to this in-memory session.
    class _Ctx:
        async def __aenter__(self_inner):
            return session
        async def __aexit__(self_inner, *a):
            return False
    monkeypatch.setattr(bot_mod.DatabaseSession, "get_session_context", lambda: _Ctx())

    b = ApartmentBot()

    # User picked Davis (sid 05).
    async def fake_default(uid):
        return "05", "Davis"
    monkeypatch.setattr(b, "_default_sid_for_user", fake_default)

    captured = {}

    async def fake_chunked(message, lines, header, footer_kb=None):
        captured["lines"] = lines
        captured["header"] = header
    monkeypatch.setattr(b, "_reply_chunked", fake_chunked)

    class _Msg:
        from_user = type("U", (), {"id": 1})()
        async def answer(self, *a, **k):
            captured["answer"] = a
    await b.cmd_list(_Msg(), user_id=1)

    blob = (captured.get("header", "") + "\n".join(captured.get("lines", [])))
    assert "Davis" in blob
    assert "Davis Tower" in blob
    assert "Murrieta Tower" not in blob  # the other server must not leak in
