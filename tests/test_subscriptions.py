"""Tests for per-user realestate subscriptions (repo + server resolution)."""

import pytest

from app.database.repository import SubscriptionRepository
from app.scraper.realestate_client import (
    resolve_servers,
    server_name_to_sid,
    sid_to_server_name,
)


# ---- Server name <-> sid helpers ----

def test_server_name_to_sid_roundtrip():
    sid = server_name_to_sid("Murrieta")
    assert sid == "20"
    assert sid_to_server_name(sid) == "Murrieta"


def test_server_name_to_sid_case_insensitive():
    assert server_name_to_sid("strawberry") == server_name_to_sid("Strawberry")


def test_server_name_to_sid_unknown_returns_none():
    assert server_name_to_sid("NotAServer") is None
    assert sid_to_server_name("99") is None


def test_resolve_servers_dedupes_and_drops_unknown():
    resolved = resolve_servers(["Murrieta", "murrieta", "Strawberry", "Bogus"])
    # Duplicate collapses by sid; unknown dropped.
    assert resolved == {"20": "Murrieta", "02": "Strawberry"}


# ---- SubscriptionRepository ----

@pytest.mark.asyncio
async def test_subscribe_is_idempotent_and_updates_kind(session):
    repo = SubscriptionRepository(session)

    first = await repo.subscribe(user_id=111, server_sid="20", kind="any")
    assert first.id is not None

    # Re-subscribing the same user+server updates in place (no duplicate row).
    again = await repo.subscribe(user_id=111, server_sid="20", kind="house")
    assert again.id == first.id
    assert again.kind == "house"

    subs = await repo.list_for_user(111)
    assert len(subs) == 1
    assert subs[0].kind == "house"


@pytest.mark.asyncio
async def test_unsubscribe_removes_only_matching(session):
    repo = SubscriptionRepository(session)
    await repo.subscribe(user_id=1, server_sid="20")
    await repo.subscribe(user_id=1, server_sid="02")

    removed = await repo.unsubscribe(user_id=1, server_sid="20")
    assert removed is True

    remaining = await repo.list_for_user(1)
    assert {s.server_sid for s in remaining} == {"02"}

    # Removing a non-existent subscription reports False.
    assert await repo.unsubscribe(user_id=1, server_sid="99") is False


@pytest.mark.asyncio
async def test_get_subscribers_kind_matching(session):
    repo = SubscriptionRepository(session)
    await repo.subscribe(user_id=1, server_sid="20", kind="any")
    await repo.subscribe(user_id=2, server_sid="20", kind="house")
    await repo.subscribe(user_id=3, server_sid="20", kind="apartment")
    await repo.subscribe(user_id=4, server_sid="02", kind="any")

    # A house event reaches the "any" and "house" subscribers of that server.
    house_subs = await repo.get_subscribers("20", kind="house")
    assert {s.user_id for s in house_subs} == {1, 2}

    # An apartment event reaches "any" and "apartment".
    apt_subs = await repo.get_subscribers("20", kind="apartment")
    assert {s.user_id for s in apt_subs} == {1, 3}

    # No kind filter returns everyone on the server.
    all_subs = await repo.get_subscribers("20")
    assert {s.user_id for s in all_subs} == {1, 2, 3}

    # Other servers are isolated.
    other = await repo.get_subscribers("02", kind="house")
    assert {s.user_id for s in other} == {4}


@pytest.mark.asyncio
async def test_get_subscribers_empty_when_none(session):
    repo = SubscriptionRepository(session)
    assert await repo.get_subscribers("20", kind="house") == []
