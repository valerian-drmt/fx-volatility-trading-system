"""Orchestrate N event sources in parallel, isolate failures, dedup, persist.

Cf. spec §2. Invariants:
  - 1 source crashes → log warning, the others keep going
  - 1 source times out → hard cap at ``source.timeout_seconds``
  - len(events) < expected_min_events → log warning (parser drift)
  - run_once is idempotent: re-run = inserted=0 if nothing changed
"""
from __future__ import annotations

import asyncio
import logging
import random

from api.orchestration.events.deduplicator import EventDeduplicator
from api.orchestration.events.repository import EventsRepository
from api.orchestration.events.sources.base import EventSource, RawEvent

logger = logging.getLogger(__name__)


class EventsScheduler:
    def __init__(
        self,
        sources: list[EventSource],
        repository: EventsRepository,
        deduplicator: EventDeduplicator,
        interval_hours: float = 24.0,
        jitter_minutes: float = 10.0,
        startup_delay_s: float = 30.0,
    ):
        self.sources = sources
        self.repository = repository
        self.deduplicator = deduplicator
        self.interval_hours = interval_hours
        self.jitter_minutes = jitter_minutes
        self.startup_delay_s = startup_delay_s
        self._task: asyncio.Task | None = None

    async def run_once(self) -> dict[str, int]:
        """Single fetch+dedup+upsert cycle. Returns per-source counts."""
        results = await asyncio.gather(
            *[self._fetch_safely(s) for s in self.sources],
            return_exceptions=False,
        )
        all_events: list[RawEvent] = []
        report: dict[str, int] = {}
        for source, events in zip(self.sources, results, strict=True):
            report[source.name] = len(events)
            all_events.extend(events)

        deduped = self.deduplicator.dedupe(all_events)
        inserted = await self.repository.upsert_many(deduped)
        report["_deduped_count"] = len(deduped)
        report["_inserted"] = inserted
        logger.info("events_sync_cycle_done %s", report)
        return report

    async def _fetch_safely(self, source: EventSource) -> list[RawEvent]:
        try:
            events = await asyncio.wait_for(
                source.fetch(), timeout=source.timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "source_%s_timeout after_s=%.1f", source.name, source.timeout_seconds,
            )
            return []
        except Exception as e:
            logger.warning("source_%s_failed %s: %s", source.name, type(e).__name__, e)
            return []

        if len(events) < source.expected_min_events:
            logger.warning(
                "source_%s_drift returned=%d expected_min=%d",
                source.name, len(events), source.expected_min_events,
            )
        return events

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="events_scheduler_loop")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        await asyncio.sleep(self.startup_delay_s)
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("events_scheduler_cycle_crashed")
            jitter_s = random.uniform(-self.jitter_minutes, self.jitter_minutes) * 60
            await asyncio.sleep(self.interval_hours * 3600 + jitter_s)
