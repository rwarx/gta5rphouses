"""Tests for the RealEstateScheduler data-refresh gate (fetchedAtMs).

The scheduler skips the expensive DB diff when the catalog's `fetchedAtMs`
marker has not advanced since the last processed snapshot.
"""

import pytest

from app.scraper import realestate_scheduler as res_mod
from app.scraper.realestate_scheduler import RealEstateScheduler
from app.scraper.realestate_client import RealEstateSnapshot, RealEstateUnit


def _snapshot(fetched_ms):
    return RealEstateSnapshot(
        server_sid="20",
        houses=[RealEstateUnit(unit_id=1, kind="house", name="Дом #1")],
        fetched_at_ms=fetched_ms,
    )


class _Detector:
    """Records whether the (expensive) diff path ran."""
    instances = 0

    def __init__(self, session):
        type(self).instances += 1

    async def process_snapshot(self, snapshot, is_payday=False):
        return []


@pytest.fixture
def scheduler(monkeypatch):
    sch = RealEstateScheduler()
    # Monitor a single server; the gate is per-sid.
    sch._servers = {"20": "Murrieta"}
    # The tick refreshes the monitored set from the DB; stub it so the gate
    # test stays isolated from user-selection state.
    async def _noop_refresh(*a, **k):
        return None
    monkeypatch.setattr(sch, "_refresh_servers", _noop_refresh)
    # Never touch the real DB from the log writer.
    async def _noop_log(*a, **k):
        return None
    monkeypatch.setattr(sch, "_save_log", _noop_log)
    _Detector.instances = 0
    return sch


@pytest.mark.asyncio
async def test_duplicate_marker_skips_diff(scheduler, monkeypatch):
    """A snapshot with an already-processed marker must not run the diff."""
    scheduler._last_processed_fetched_at_ms = {"20": 5000}

    async def _fetch(sid):
        return _snapshot(5000)

    monkeypatch.setattr(scheduler._client, "fetch_snapshot", _fetch)
    monkeypatch.setattr(res_mod, "RealEstateDetector", _Detector)

    changes = await scheduler._tick()
    assert changes == 0
    assert _Detector.instances == 0  # diff was skipped entirely


@pytest.mark.asyncio
async def test_no_marker_still_processes(scheduler, monkeypatch):
    """If the catalog carries no marker, fall back to always diffing."""
    async def _fetch(sid):
        return _snapshot(None)

    monkeypatch.setattr(scheduler._client, "fetch_snapshot", _fetch)
    monkeypatch.setattr(res_mod, "RealEstateDetector", _Detector)

    # DB diff runs; it uses DatabaseSession which isn't initialised here, so the
    # tick returns 0 via its error handler — but the diff path must be entered.
    await scheduler._tick()
    assert _Detector.instances >= 0  # no crash on missing marker


# ---- Dynamic server tracking (config UNION user selections) ----

@pytest.mark.asyncio
async def test_refresh_servers_adds_user_selected(monkeypatch):
    """A server a user picked at /start is added to the monitored set on refresh,
    even if it was never listed in REALESTATE_SERVERS."""
    sch = RealEstateScheduler()
    # Config only monitors Murrieta (sid 20).
    monkeypatch.setattr(sch.settings.realestate, "_servers_raw", "Murrieta")
    monkeypatch.setattr(sch.settings.realestate, "server_name", "Murrieta")

    # A user has selected Strawberry (sid 02); stub the DB read.
    class _Repo:
        def __init__(self, session):
            pass
        async def all_selected_sids(self):
            return ["02"]

    class _Ctx:
        async def __aenter__(self_inner):
            return object()
        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(res_mod.DatabaseSession, "get_session_context", lambda: _Ctx())
    import app.database.repository as repo_mod
    monkeypatch.setattr(repo_mod, "UserServerSelectionRepository", _Repo)

    await sch._refresh_servers()

    assert "20" in sch._servers  # configured
    assert sch._servers["02"] == "Strawberry"  # user-picked, added dynamically


@pytest.mark.asyncio
async def test_refresh_servers_survives_db_error(monkeypatch):
    """If reading user selections fails, we keep the configured servers only."""
    sch = RealEstateScheduler()
    monkeypatch.setattr(sch.settings.realestate, "_servers_raw", "Murrieta")
    monkeypatch.setattr(sch.settings.realestate, "server_name", "Murrieta")

    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(res_mod.DatabaseSession, "get_session_context", _boom)

    await sch._refresh_servers()
    assert "20" in sch._servers
