"""Tests for the field-comparison logic of the ChangeDetector."""

import pytest

from app.scraper.change_detector import ChangeDetector
from app.scraper.playwright_scraper import ApartmentData


@pytest.mark.asyncio
async def test_no_previous_data_yields_no_changes(session):
    detector = ChangeDetector(session)
    data = ApartmentData(name="Seoul Towers", free_apartments=3, total_apartments=10)
    changes = await detector._compare_fields(apartment=None, new_data=data, previous_data=None)
    assert changes == []


@pytest.mark.asyncio
async def test_detects_free_count_change(session):
    detector = ChangeDetector(session)
    data = ApartmentData(name="Seoul Towers", free_apartments=5, total_apartments=10)
    previous = {"free_apartments": 2, "total_apartments": 10, "raw_data": {}}
    changes = await detector._compare_fields(apartment=None, new_data=data, previous_data=previous)
    fields = {c.field_name: c for c in changes}
    assert "free_apartments" in fields
    assert fields["free_apartments"].old_value == "2"
    assert fields["free_apartments"].new_value == "5"


@pytest.mark.asyncio
async def test_no_change_when_values_equal(session):
    detector = ChangeDetector(session)
    data = ApartmentData(name="Seoul Towers", free_apartments=2, total_apartments=10)
    previous = {"free_apartments": 2, "total_apartments": 10, "raw_data": {}}
    changes = await detector._compare_fields(apartment=None, new_data=data, previous_data=previous)
    assert changes == []


@pytest.mark.asyncio
async def test_detects_new_raw_field(session):
    detector = ChangeDetector(session)
    data = ApartmentData(name="Seoul Towers")
    data.raw_data = {"all_fields": {"balcony": "yes"}}
    previous = {"raw_data": {"all_fields": {}}}
    changes = await detector._compare_fields(apartment=None, new_data=data, previous_data=previous)
    assert any(c.field_name == "new_field_balcony" for c in changes)
