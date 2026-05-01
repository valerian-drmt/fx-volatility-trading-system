"""Tests for the hardcoded/computed calendar sources : ECB, BoE, FOMC,
Eurostat, ONS. No network."""
from __future__ import annotations

import pytest

from api.orchestration.events.sources.boe import BoESource
from api.orchestration.events.sources.ecb import ECBSource
from api.orchestration.events.sources.eurostat import EurostatSource
from api.orchestration.events.sources.fomc import FOMCSource
from api.orchestration.events.sources.ons import ONSSource


@pytest.mark.asyncio
async def test_ecb_returns_at_least_4_future_meetings():
    events = await ECBSource().fetch()
    assert len(events) >= 4
    assert all(e.region == "EU" and e.event_type == "ECB" for e in events)
    assert all(e.scheduled_at.tzinfo is not None for e in events)


@pytest.mark.asyncio
async def test_ecb_decisions_at_14_15_cet_or_cest():
    """ECB statement at 14:15 local Brussels → 12:15 UTC (CET) or 13:15 (CEST)."""
    events = await ECBSource().fetch()
    for e in events:
        assert e.scheduled_at.hour in (12, 13)
        assert e.scheduled_at.minute == 15


@pytest.mark.asyncio
async def test_boe_returns_future_meetings():
    events = await BoESource().fetch()
    assert len(events) >= 4
    assert all(e.region == "GB" and e.event_type == "BOE" for e in events)


@pytest.mark.asyncio
async def test_boe_announcement_at_12_00_uk():
    """BoE bank rate at 12:00 UK → 11:00 UTC (BST) or 12:00 (GMT)."""
    events = await BoESource().fetch()
    for e in events:
        assert e.scheduled_at.hour in (11, 12)
        assert e.scheduled_at.minute == 0


@pytest.mark.asyncio
async def test_fomc_returns_decisions_and_minutes():
    events = await FOMCSource().fetch()
    types = {e.event_type for e in events}
    # At least one of each — meeting + minutes both pushed.
    assert types <= {"FOMC", "FOMC_minutes"}
    decisions = [e for e in events if e.event_type == "FOMC"]
    minutes = [e for e in events if e.event_type == "FOMC_minutes"]
    assert len(decisions) >= 4
    assert len(minutes) >= 4


@pytest.mark.asyncio
async def test_fomc_decision_at_14_00_eastern():
    """FOMC statement at 14:00 ET → 18:00 UTC (EDT) or 19:00 (EST)."""
    events = [e for e in await FOMCSource().fetch() if e.event_type == "FOMC"]
    for e in events:
        assert e.scheduled_at.hour in (18, 19)
        assert e.scheduled_at.minute == 0


@pytest.mark.asyncio
async def test_eurostat_covers_hicp_and_gdp():
    events = await EurostatSource().fetch()
    types = {e.event_type for e in events}
    assert "CPI_FLASH_EU" in types
    assert "CPI_EU" in types
    # GDP_EU appears 4 times/year → ≥1 within 6 months
    assert "GDP_EU" in types


@pytest.mark.asyncio
async def test_ons_covers_cpi_and_gdp():
    events = await ONSSource().fetch()
    types = {e.event_type for e in events}
    assert "CPI_GB" in types
    assert "GDP_GB" in types
    assert all(e.region == "GB" for e in events)


@pytest.mark.asyncio
async def test_all_sources_emit_tz_aware_utc():
    """Required by hashing.event_hash (uses tz info to truncate to minute)."""
    for src in [ECBSource(), BoESource(), FOMCSource(), EurostatSource(), ONSSource()]:
        events = await src.fetch()
        for e in events:
            assert e.scheduled_at.tzinfo is not None
            assert e.scheduled_at.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_all_sources_only_emit_future_events():
    """Past events must be filtered — keeps the table clean of garbage."""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    for src in [ECBSource(), BoESource(), FOMCSource(), EurostatSource(), ONSSource()]:
        events = await src.fetch()
        for e in events:
            assert e.scheduled_at > now, f"{src.name} emitted past event {e}"
