"""Tests for the SmartScheduler pure decision logic (no browser/DB needed)."""

import pytest

from app.scraper.scheduler import SmartScheduler, MonitorMode


@pytest.fixture
def scheduler():
    return SmartScheduler()


class _FakeGateClient:
    """Stand-in RealEstateClient returning a scripted sequence of markers."""

    def __init__(self, markers):
        self._markers = list(markers)
        self.calls = 0

    async def fetch_updated_ms(self, sid):
        self.calls += 1
        if not self._markers:
            return None
        val = self._markers[0]
        if len(self._markers) > 1:
            self._markers.pop(0)
        if isinstance(val, Exception):
            raise val
        return val


def test_determine_mode_inside_wraparound_window(scheduler):
    # Default window is 59 -> 10 (wraps past the top of the hour).
    scheduler.settings.smart_mode.payday_start_minute = 59
    scheduler.settings.smart_mode.payday_end_minute = 10
    assert scheduler._determine_mode(59) == MonitorMode.HIGH
    assert scheduler._determine_mode(5) == MonitorMode.HIGH
    assert scheduler._determine_mode(10) == MonitorMode.HIGH


def test_determine_mode_outside_window_is_low(scheduler):
    scheduler.settings.smart_mode.payday_start_minute = 59
    scheduler.settings.smart_mode.payday_end_minute = 10
    scheduler._consecutive_failures = 0
    assert scheduler._determine_mode(30) == MonitorMode.LOW


def test_determine_mode_non_wraparound_window(scheduler):
    scheduler.settings.smart_mode.payday_start_minute = 20
    scheduler.settings.smart_mode.payday_end_minute = 40
    assert scheduler._determine_mode(30) == MonitorMode.HIGH
    scheduler._consecutive_failures = 0
    assert scheduler._determine_mode(10) == MonitorMode.LOW
    assert scheduler._determine_mode(50) == MonitorMode.LOW


def test_determine_mode_recovery_on_repeated_failures(scheduler):
    scheduler.settings.smart_mode.payday_start_minute = 20
    scheduler.settings.smart_mode.payday_end_minute = 40
    scheduler._consecutive_failures = 3
    # Outside the payday window, repeated failures trigger recovery mode.
    assert scheduler._determine_mode(10) == MonitorMode.RECOVERY


def test_get_interval_per_mode(scheduler):
    scheduler.settings.smart_mode.high_interval = 5
    scheduler.settings.smart_mode.low_interval = 600
    assert scheduler._get_interval(MonitorMode.HIGH) == 5
    assert scheduler._get_interval(MonitorMode.LOW) == 600
    assert scheduler._get_interval(MonitorMode.RECOVERY) == 30


def test_stats_shape(scheduler):
    stats = scheduler.stats
    for key in (
        "total_runs",
        "successful_runs",
        "failed_runs",
        "current_mode",
        "is_running",
    ):
        assert key in stats


@pytest.mark.asyncio
async def test_map_gate_disabled_always_scrapes(scheduler):
    """With no gate client configured, every tick is allowed to scrape."""
    scheduler._map_gate_client = None
    assert await scheduler._should_scrape() is True


@pytest.mark.asyncio
async def test_map_gate_skips_when_marker_unchanged(scheduler):
    """Same fetchedAtMs across ticks -> scrape only once, skip the rest."""
    scheduler._map_gate_client = _FakeGateClient([1000, 1000, 1000])
    scheduler._map_gate_sid = "20"

    assert await scheduler._should_scrape() is True   # first sighting -> scrape
    assert scheduler._last_scraped_fetched_at_ms == 1000
    assert await scheduler._should_scrape() is False  # unchanged -> skip
    assert await scheduler._should_scrape() is False


@pytest.mark.asyncio
async def test_map_gate_scrapes_when_marker_advances(scheduler):
    """A new fetchedAtMs (catalog recomputed) re-enables the scrape."""
    scheduler._map_gate_client = _FakeGateClient([1000, 2000])
    scheduler._map_gate_sid = "20"

    assert await scheduler._should_scrape() is True
    assert await scheduler._should_scrape() is True
    assert scheduler._last_scraped_fetched_at_ms == 2000


@pytest.mark.asyncio
async def test_map_gate_fails_open_on_error(scheduler):
    """A marker read error must not block scraping (fail open)."""
    scheduler._map_gate_client = _FakeGateClient([RuntimeError("boom")])
    scheduler._map_gate_sid = "20"
    assert await scheduler._should_scrape() is True


@pytest.mark.asyncio
async def test_map_gate_fails_open_on_none_marker(scheduler):
    """A missing marker must not block scraping (fail open)."""
    scheduler._map_gate_client = _FakeGateClient([None])
    scheduler._map_gate_sid = "20"
    assert await scheduler._should_scrape() is True
