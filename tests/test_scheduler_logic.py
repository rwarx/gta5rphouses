"""Tests for the SmartScheduler pure decision logic (no browser/DB needed)."""

import pytest

from app.scraper.scheduler import SmartScheduler, MonitorMode


@pytest.fixture
def scheduler():
    return SmartScheduler()


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
