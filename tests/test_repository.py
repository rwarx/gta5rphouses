"""Tests for database repositories against an in-memory SQLite database."""

import pytest

from app.database.repository import (
    ApartmentRepository,
    ChangeRepository,
    ScraperSettingsRepository,
)


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(session):
    repo = ApartmentRepository(session)

    created = await repo.upsert("seoul", {"name": "Seoul Towers", "free_apartments": 2, "total_apartments": 10})
    assert created.id is not None
    assert created.free_apartments == 2

    updated = await repo.upsert("seoul", {"name": "Seoul Towers", "free_apartments": 7, "total_apartments": 10})
    assert updated.id == created.id
    assert updated.free_apartments == 7

    all_apts = await repo.get_all()
    assert len(all_apts) == 1


@pytest.mark.asyncio
async def test_get_free_apartments_filters_zero(session):
    repo = ApartmentRepository(session)
    await repo.upsert("a", {"name": "Has free", "free_apartments": 3, "total_apartments": 5})
    await repo.upsert("b", {"name": "Full", "free_apartments": 0, "total_apartments": 5})
    await session.commit()

    free = await repo.get_free_apartments()
    names = {a.name for a in free}
    assert names == {"Has free"}


@pytest.mark.asyncio
async def test_search_by_name_and_address(session):
    repo = ApartmentRepository(session)
    await repo.upsert("a", {"name": "Seoul Towers", "address": "Vinewood"})
    await repo.upsert("b", {"name": "Del Perro", "address": "Beach"})
    await session.commit()

    assert {a.name for a in await repo.search("seoul")} == {"Seoul Towers"}
    assert {a.name for a in await repo.search("beach")} == {"Del Perro"}


@pytest.mark.asyncio
async def test_statistics_aggregate(session):
    repo = ApartmentRepository(session)
    await repo.upsert("a", {"name": "A", "free_apartments": 3, "occupied_apartments": 2})
    await repo.upsert("b", {"name": "B", "free_apartments": 1, "occupied_apartments": 4})
    await session.commit()

    stats = await repo.get_statistics()
    assert stats["total_apartments"] == 2
    assert stats["total_free"] == 4
    assert stats["total_occupied"] == 6


@pytest.mark.asyncio
async def test_scraper_settings_get_set(session):
    repo = ScraperSettingsRepository(session)
    assert await repo.get("missing") is None

    await repo.set("notify_free_found", "1")
    await session.commit()
    assert await repo.get("notify_free_found") == "1"

    await repo.set("notify_free_found", "0")
    await session.commit()
    assert await repo.get("notify_free_found") == "0"
