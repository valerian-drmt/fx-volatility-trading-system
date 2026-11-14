"""#307, D2: one calendar from the database rows and the cached FRED list."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from api.orchestration.events.deduplicator import EventDeduplicator
from api.orchestration.events.scheduler import EventsScheduler
from api.orchestration.events.sources.base import EventSource, RawEvent
from bus.fred_cache import FRED_EVENTS_KEY, FRED_EVENTS_TTL_S, load_fred_events, store_fred_events
from core.calendar_merge import merge_events, next_event

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


class _Redis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        self.ttl[key] = int(ex or 0)

    async def get(self, key: str) -> str | None:
        return self.data.get(key)


def _e(kind: str, days: float, *, region: str = "US", impact: str = "high", source: str = "FRED",
       **extra: Any) -> dict[str, Any]:
    return {"event_type": kind, "impact": impact, "region": region, "scheduled_at": NOW + timedelta(days=days),
            "description": kind, "source": source, **extra}


class _Source(EventSource):
    def __init__(self, name: str, events: list[RawEvent]) -> None:
        self.name = name
        self._events = events

    async def fetch(self) -> list[RawEvent]:
        return self._events


def _raw(kind: str, source: str, days: float = 3) -> RawEvent:
    return RawEvent(event_type=kind, region="US", impact="high", scheduled_at=NOW + timedelta(days=days),
                    description=kind, source_name=source)


async def test_fred_events_go_to_redis_for_24_hours_and_never_to_the_database() -> None:
    repo = AsyncMock()
    repo.upsert_many.return_value = 1
    redis = _Redis()
    scheduler = EventsScheduler(
        [_Source("FRED", [_raw("CPI", "FRED"), _raw("NFP", "FRED", 5)]), _Source("ECB", [_raw("ECB", "ECB")])],
        repo, EventDeduplicator(), redis_getter=lambda: redis,
    )
    report = await scheduler.run_once()
    stored = [e.source_name for _h, e in repo.upsert_many.call_args.args[0]]
    assert stored == ["ECB"], "no FRED row reaches the repository"
    assert report["_fred_cached"] == 2
    assert redis.ttl[FRED_EVENTS_KEY] == FRED_EVENTS_TTL_S == 86400
    assert {e["event_type"] for e in json.loads(redis.data[FRED_EVENTS_KEY])} == {"CPI", "NFP"}
    assert scheduler.interval_hours == 12.0, "refreshed twice within the cache's life"


async def test_a_failed_fetch_does_not_erase_a_list_still_valid() -> None:
    redis = _Redis()
    assert await store_fred_events(redis, [_e("CPI", 3)]) == 1
    assert await store_fred_events(redis, []) == 0
    loaded = await load_fred_events(redis)
    assert [e["event_type"] for e in loaded] == ["CPI"] and loaded[0]["scheduled_at"] == NOW + timedelta(days=3)


async def test_the_calendar_still_answers_without_the_cache() -> None:
    class _Broken:
        async def get(self, _key: str) -> str:
            raise ConnectionError("redis down")

    assert await load_fred_events(None) == []
    assert await load_fred_events(_Broken()) == []
    assert await load_fred_events(_Redis()) == []


def test_both_stores_read_as_one_calendar() -> None:
    db = [_e("ECB", 5, region="EU", source="ECB", id=7), _e("FOMC", 2, source="FOMC", id=8)]
    cached = [_e("CPI", 1), _e("FOMC", 2)]            # FOMC is in both
    merged = merge_events(db, cached)
    assert [(e["event_type"], e["source"], e["id"]) for e in merged] == [
        ("CPI", "FRED", None), ("FOMC", "FOMC", 8), ("ECB", "ECB", 7),
    ], "ordered by time; the database row wins over the same event from the cache"
    assert next_event(merged, NOW)["event_type"] == "CPI"
    assert next_event(merged, NOW, regions=("EU",))["event_type"] == "ECB"
    assert next_event(merged, NOW + timedelta(days=9)) is None
    assert next_event([_e("M2", 1, impact="low")], NOW, impact="high") is None


def test_a_time_without_a_zone_is_read_as_utc() -> None:
    naive = {**_e("ECB", 5, region="EU", source="ECB"), "scheduled_at": (NOW + timedelta(days=5)).replace(tzinfo=None)}
    merged = merge_events([naive], [_e("CPI", 1)])
    assert [e["event_type"] for e in merged] == ["CPI", "ECB"]
    assert all(e["scheduled_at"].tzinfo is not None for e in merged)
