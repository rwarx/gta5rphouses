"""
Tests for the Telegram inline-menu keyboards and callback-data scheme.

These exercise the pure keyboard builders and the callback-data conventions
without touching Telegram or the network. They guard the menu structure and,
crucially, the invariant that every submenu offers a way back.
"""

import os

# Force an isolated environment before app.config's cached settings load.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("BOT_TOKEN", "")

import pytest

from app.telegram.bot import ApartmentBot, BotStates


def _all_callbacks(markup):
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


@pytest.fixture
def bot():
    return ApartmentBot()


def test_main_menu_has_core_sections(bot):
    cbs = _all_callbacks(bot._get_keyboard(0))
    assert "menu:apartments" in cbs
    assert "menu:catalog" in cbs
    assert "menu:subs" in cbs
    assert "menu:reports" in cbs


def test_main_menu_hides_admin_for_non_admin(bot, monkeypatch):
    # Restrict admins so a random uid is not one.
    monkeypatch.setattr(bot, "_is_admin", lambda uid: False)
    cbs = _all_callbacks(bot._get_keyboard(12345))
    assert "menu:admin" not in cbs


def test_main_menu_shows_admin_for_admin(bot, monkeypatch):
    monkeypatch.setattr(bot, "_is_admin", lambda uid: True)
    cbs = _all_callbacks(bot._get_keyboard(999))
    assert "menu:admin" in cbs


@pytest.mark.parametrize("section", ["apartments", "catalog", "reports", "admin"])
def test_every_submenu_has_a_back_button(bot, monkeypatch, section):
    monkeypatch.setattr(bot, "_is_admin", lambda uid: True)
    cbs = _all_callbacks(bot._menu_markup(section, 1))
    assert "menu:main" in cbs, f"{section} menu is missing a back button"


def test_reports_menu_hides_admin_toggles_for_non_admin(bot, monkeypatch):
    monkeypatch.setattr(bot, "_is_admin", lambda uid: False)
    cbs = _all_callbacks(bot._menu_markup("reports", 5))
    assert "adm:report_toggle" not in cbs
    assert "act:report_now" in cbs  # everyone can request a report


def test_back_kb_targets_requested_section(bot):
    cbs = _all_callbacks(bot._back_kb("catalog"))
    assert cbs == ["menu:catalog"]


def test_sub_kind_markup_encodes_server_and_kind(bot):
    cbs = _all_callbacks(bot._sub_kind_markup("20"))
    assert "sub:add:20:any" in cbs
    assert "sub:class:20" in cbs  # house → class picker
    assert "sub:add:20:apartment" in cbs
    assert "menu:subpick" in cbs  # back


def test_sub_class_markup_encodes_house_and_class(bot):
    cbs = _all_callbacks(bot._sub_class_markup("20"))
    assert "sub:add:20:house" in cbs  # any class
    assert "sub:add:20:house:Престиж" in cbs
    assert "sub:add:20:house:Стандарт" in cbs
    assert "sub:add:20:house:Эконом" in cbs
    assert "sub:add:20:house:Комфорт" in cbs
    assert "sub:add:20:house:Премиум" in cbs
    assert "subkind:20" in cbs


def test_sub_add_callback_parses_kind_and_class(bot):
    # Without class_name
    parts = "sub:add:20:apartment".split(":")
    assert parts[2] == "20"
    assert parts[3] == "apartment"
    assert len(parts) == 4
    # With class_name
    parts = "sub:add:11:house:Престиж".split(":")
    assert parts[2] == "11"
    assert parts[3] == "house"
    assert parts[4] == "Престиж"
    assert len(parts) == 5


def test_sub_del_callback_parses_sid_and_kind(bot):
    # With class_name
    parts = "sub:del:20:house:Престиж".split(":")
    assert parts[2] == "20"
    assert parts[3] == "house"
    assert parts[4] == "Престиж"
    # Without class_name
    parts = "sub:del:20:apartment".split(":")
    assert parts[2] == "20"
    assert parts[3] == "apartment"
    assert len(parts) == 4
    # Backward compat (no kind)
    parts = "sub:del:20".split(":")
    assert parts[2] == "20"
    assert len(parts) == 3


def test_bot_states_cover_all_text_inputs():
    for name in ("search", "status", "history", "owners", "building", "owner_history"):
        assert hasattr(BotStates, name)
