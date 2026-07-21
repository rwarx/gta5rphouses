"""Tests for subscription-aware notification routing in ChangeNotifier."""

import pytest
from datetime import timedelta

from app.database.repository import SubscriptionRepository
from app.telegram.notifier import ChangeNotifier, format_duration


def _make_notifier(allowed_users):
    """Build a ChangeNotifier without running its __init__ side effects."""
    notifier = ChangeNotifier.__new__(ChangeNotifier)

    class _Tg:
        pass

    class _Settings:
        pass

    tg = _Tg()
    tg.allowed_users = allowed_users
    settings = _Settings()
    settings.telegram = tg
    notifier.settings = settings
    notifier.bot = None
    notifier._pending_edits = {}
    return notifier


@pytest.mark.asyncio
async def test_recipients_prefers_subscribers(session):
    repo = SubscriptionRepository(session)
    await repo.subscribe(user_id=555, server_sid="20", kind="any")
    await repo.subscribe(user_id=556, server_sid="20", kind="house")

    notifier = _make_notifier(allowed_users=[1, 2])
    recipients = await notifier._recipients_for_server(session, "20", kind="house")
    assert set(recipients) == {555, 556}


@pytest.mark.asyncio
async def test_recipients_kind_filter(session):
    repo = SubscriptionRepository(session)
    await repo.subscribe(user_id=1, server_sid="20", kind="apartment")

    notifier = _make_notifier(allowed_users=[99])
    # A house event has no apartment-only subscriber -> no one gets it.
    recipients = await notifier._recipients_for_server(session, "20", kind="house")
    assert recipients == []


@pytest.mark.asyncio
async def test_recipients_empty_when_no_subscribers(session):
    notifier = _make_notifier(allowed_users=[1, 2, 3])
    recipients = await notifier._recipients_for_server(session, "20", kind="house")
    assert recipients == []


@pytest.mark.asyncio
async def test_recipients_empty_when_no_sid(session):
    notifier = _make_notifier(allowed_users=[7])
    recipients = await notifier._recipients_for_server(session, None)
    assert recipients == []


# ---- format_duration ----

def test_format_duration_less_than_minute():
    assert format_duration(timedelta(seconds=30)) == "менее минуты"


def test_format_duration_minutes():
    result = format_duration(timedelta(minutes=5))
    assert "5" in result and "минут" in result


def test_format_duration_hours():
    result = format_duration(timedelta(hours=3, minutes=15))
    assert "3" in result and "часа" in result


def test_format_duration_days():
    result = format_duration(timedelta(days=7, hours=2))
    assert "7" in result and "дней" in result


def test_format_duration_1_day():
    result = format_duration(timedelta(days=1))
    assert "1 день" in result


def test_format_duration_2_days():
    result = format_duration(timedelta(days=2, hours=5))
    assert "2 дня" in result and "5 часов" in result


# ---- _owner_history_kb ----

def test_owner_history_kb_has_button():
    notifier = _make_notifier(allowed_users=[1])
    kb = notifier._owner_history_kb("20:house:42")
    assert kb.inline_keyboard
    btn = kb.inline_keyboard[0][0]
    assert "📜 История владельцев" in btn.text
    assert btn.callback_data == "hst:20:house:42"
