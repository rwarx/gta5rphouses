"""Tests for subscription-aware notification routing in ChangeNotifier."""

import pytest

from app.database.repository import SubscriptionRepository
from app.telegram.notifier import ChangeNotifier


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
