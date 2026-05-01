"""Scheduler isolation tests — uses FakeSource, no network."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from api.orchestration.events.deduplicator import EventDeduplicator
from api.orchestration.events.scheduler import EventsScheduler
from api.orchestration.events.sources.base import EventSource, RawEvent


class FakeSource(EventSource):
    expected_min_events = 1

    def __init__(self, name: str, events=None, raises=None, sleep_s: float = 0.0):
        self.name = name
        self.timeout_seconds = 5.0
        self._events = events or []
        self._raises = raises
        self._sleep_s = sleep_s

    async def fetch(self) -> list[RawEvent]:
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
        if self._raises:
            raise self._raises
        return self._events


def _ev(event_type: str, region: str = "US") -> RawEvent:
    return RawEvent(
        event_type=event_type, region=region, impact="high",
        scheduled_at=datetime(2026, 5, 2, 12, 30, tzinfo=UTC),
        description=f"{event_type} test", source_name="fake",
    )


@pytest.mark.asyncio
async def test_scheduler_isolates_failing_source():
    good = FakeSource("good", events=[_ev("FOMC")])
    bad = FakeSource("bad", raises=RuntimeError("boom"))
    repo = AsyncMock()
    repo.upsert_many.return_value = 1
    scheduler = EventsScheduler([good, bad], repo, EventDeduplicator())
    report = await scheduler.run_once()
    assert report["good"] == 1
    assert report["bad"] == 0
    assert report["_inserted"] == 1


@pytest.mark.asyncio
async def test_scheduler_caps_slow_source():
    slow = FakeSource("slow", sleep_s=10.0)
    slow.timeout_seconds = 0.1
    fast = FakeSource("fast", events=[_ev("CPI")])
    repo = AsyncMock()
    repo.upsert_many.return_value = 1
    scheduler = EventsScheduler([slow, fast], repo, EventDeduplicator())
    report = await scheduler.run_once()
    assert report["slow"] == 0
    assert report["fast"] == 1


@pytest.mark.asyncio
async def test_scheduler_dedupes_same_event_from_two_sources():
    a = FakeSource("a", events=[_ev("NFP")])
    b = FakeSource("b", events=[_ev("NFP")])  # same identity
    repo = AsyncMock()
    repo.upsert_many.return_value = 1
    scheduler = EventsScheduler([a, b], repo, EventDeduplicator())
    report = await scheduler.run_once()
    assert report["a"] == 1
    assert report["b"] == 1
    assert report["_deduped_count"] == 1
    repo.upsert_many.assert_awaited_once()
    args = repo.upsert_many.await_args[0][0]
    assert len(args) == 1


@pytest.mark.asyncio
async def test_scheduler_empty_when_all_sources_fail():
    a = FakeSource("a", raises=RuntimeError("x"))
    b = FakeSource("b", raises=ValueError("y"))
    repo = AsyncMock()
    repo.upsert_many.return_value = 0
    scheduler = EventsScheduler([a, b], repo, EventDeduplicator())
    report = await scheduler.run_once()
    assert report["_inserted"] == 0
    assert report["_deduped_count"] == 0
