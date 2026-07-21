"""Intra-cycle dedup: 2 sources returning the same CPI → only 1 kept.

Inter-cycle dedup = UNIQUE constraint in the DB (cf. migration 012).
"""
from __future__ import annotations

from api.orchestration.events.hashing import event_hash
from api.orchestration.events.sources.base import RawEvent


class EventDeduplicator:
    """First-seen-wins on (type, region, minute). Order = collection order."""

    def dedupe(self, events: list[RawEvent]) -> list[tuple[str, RawEvent]]:
        seen: dict[str, RawEvent] = {}
        for e in events:
            h = event_hash(e)
            if h not in seen:
                seen[h] = e
        return list(seen.items())
