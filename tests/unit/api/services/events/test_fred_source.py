"""FRED parser tests — fixture-based, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.services.events.sources.fred import (
    FRED_HIGH_IMPACT_RELEASES,
    FREDSource,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fred_response.json"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parser_keeps_only_whitelisted_releases(payload):
    src = FREDSource(api_key="test")
    events = src._parse(payload)
    # Fixture has 5 whitelisted + 1 unknown (release_id=999)
    assert len(events) == 5
    types = sorted(e.event_type for e in events)
    assert types == ["CPI", "FOMC", "GDP", "NFP", "RetailSales"]


def test_parser_all_events_are_us_region(payload):
    src = FREDSource(api_key="test")
    events = src._parse(payload)
    assert all(e.region == "US" for e in events)


def test_parser_scheduled_at_is_tz_aware(payload):
    src = FREDSource(api_key="test")
    events = src._parse(payload)
    for e in events:
        assert e.scheduled_at.tzinfo is not None
        assert e.scheduled_at.utcoffset().total_seconds() == 0  # UTC


def test_parser_nfp_release_at_8_30_eastern_utc():
    """NFP @ 8:30 ET → 12:30 UTC during EDT, 13:30 UTC during EST."""
    src = FREDSource(api_key="test")
    payload = {"release_dates": [
        {"release_id": 50, "date": "2026-05-02", "release_name": "Employment Situation"},
    ]}
    events = src._parse(payload)
    assert len(events) == 1
    # 2026-05-02 = EDT (DST active, UTC-4) → 8:30 ET = 12:30 UTC
    assert events[0].scheduled_at.hour == 12
    assert events[0].scheduled_at.minute == 30


def test_parser_fomc_at_14_00_eastern():
    """FOMC statement at 14:00 ET → 18:00 UTC during EDT."""
    src = FREDSource(api_key="test")
    payload = {"release_dates": [
        {"release_id": 101, "date": "2026-05-14", "release_name": "Selected Interest Rates"},
    ]}
    events = src._parse(payload)
    assert len(events) == 1
    assert events[0].scheduled_at.hour == 18
    assert events[0].scheduled_at.minute == 0


def test_parser_skips_unknown_release_ids(payload):
    src = FREDSource(api_key="test")
    events = src._parse(payload)
    assert all(e.event_type in {v[0] for v in FRED_HIGH_IMPACT_RELEASES.values()} for e in events)


def test_parser_handles_empty_payload():
    src = FREDSource(api_key="test")
    assert src._parse({"release_dates": []}) == []
    assert src._parse({}) == []


def test_parser_handles_malformed_date():
    src = FREDSource(api_key="test")
    payload = {"release_dates": [
        {"release_id": 50, "date": "not-a-date", "release_name": "x"},
        {"release_id": 50, "date": "2026-05-02", "release_name": "y"},
    ]}
    events = src._parse(payload)
    assert len(events) == 1  # skip malformed, keep valid
