"""Tests for the /realestate catalog diff logic (RealEstateDetector)."""

import pytest

from app.database.repository import RealEstateRepository
from app.scraper.realestate_client import RealEstateSnapshot, RealEstateUnit
from app.scraper.realestate_detector import RealEstateDetector


def _house(unit_id: int, owner: str = "Alice", **kw) -> RealEstateUnit:
    return RealEstateUnit(
        unit_id=unit_id, kind="house", name=f"Дом #{unit_id}",
        price=95000, class_name="Стандарт", owner_name=owner, **kw
    )


def _snapshot(sid: str, houses=None, apartments=None) -> RealEstateSnapshot:
    return RealEstateSnapshot(
        server_sid=sid, houses=houses or [], apartments=apartments or [],
    )


@pytest.mark.asyncio
async def test_first_run_is_baseline_only(session):
    """First snapshot seeds state without emitting events."""
    detector = RealEstateDetector(session)
    changes = await detector.process_snapshot(
        _snapshot("20", houses=[_house(1), _house(2)])
    )
    assert changes == []
    repo = RealEstateRepository(session)
    assert await repo.count_occupied("20") == 2


@pytest.mark.asyncio
async def test_disappeared_object_is_freed(session):
    """An object present before but absent now yields a 'freed' event."""
    detector = RealEstateDetector(session)
    await detector.process_snapshot(_snapshot("20", houses=[_house(1), _house(2)]))

    changes = await detector.process_snapshot(_snapshot("20", houses=[_house(1)]))

    freed = [c for c in changes if c.event_type == "freed"]
    assert len(freed) == 1
    assert freed[0].object_key == "20:house:2"
    assert freed[0].old_owner == "Alice"
    # The freed object is no longer counted as occupied.
    repo = RealEstateRepository(session)
    assert await repo.count_occupied("20") == 1


@pytest.mark.asyncio
async def test_new_object_is_occupied(session):
    """An object appearing after the baseline yields an 'occupied' event."""
    detector = RealEstateDetector(session)
    await detector.process_snapshot(_snapshot("20", houses=[_house(1)]))

    changes = await detector.process_snapshot(
        _snapshot("20", houses=[_house(1), _house(3, owner="Bob")])
    )
    occupied = [c for c in changes if c.event_type == "occupied"]
    assert len(occupied) == 1
    assert occupied[0].object_key == "20:house:3"
    assert occupied[0].new_owner == "Bob"


@pytest.mark.asyncio
async def test_owner_change_is_detected(session):
    """A kept object whose owner changed yields an 'owner_changed' event."""
    detector = RealEstateDetector(session)
    await detector.process_snapshot(_snapshot("20", houses=[_house(1, owner="Alice")]))

    changes = await detector.process_snapshot(
        _snapshot("20", houses=[_house(1, owner="Carol")])
    )
    owner_changes = [c for c in changes if c.event_type == "owner_changed"]
    assert len(owner_changes) == 1
    assert owner_changes[0].old_owner == "Alice"
    assert owner_changes[0].new_owner == "Carol"


@pytest.mark.asyncio
async def test_empty_snapshot_is_skipped(session):
    """A zero-object snapshot is treated as a fetch failure, not a mass freeing."""
    detector = RealEstateDetector(session)
    await detector.process_snapshot(_snapshot("20", houses=[_house(1), _house(2)]))

    changes = await detector.process_snapshot(_snapshot("20"))
    assert changes == []
    # Nothing was marked freed.
    repo = RealEstateRepository(session)
    assert await repo.count_occupied("20") == 2


@pytest.mark.asyncio
async def test_unnotified_events_are_queued(session):
    """Detected events land in the unnotified queue for the notifier."""
    detector = RealEstateDetector(session)
    await detector.process_snapshot(_snapshot("20", houses=[_house(1), _house(2)]))
    await detector.process_snapshot(_snapshot("20", houses=[_house(1)]))

    repo = RealEstateRepository(session)
    pending = await repo.get_unnotified_events()
    assert len(pending) == 1
    assert pending[0].event_type == "freed"


def test_server_name_to_sid():
    """Murrieta maps to its zero-padded catalog sid."""
    from app.scraper.realestate_client import server_name_to_sid
    assert server_name_to_sid("Murrieta") == "20"
    assert server_name_to_sid("downtown") == "01"
    assert server_name_to_sid("Nonexistent") is None
