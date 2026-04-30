"""Intra-cycle dedup : 2 sources qui renvoient le même CPI → 1 seul gardé.

Inter-cycle dedup = contrainte UNIQUE en DB (cf. migration 012).
"""
from __future__ import annotations

from api.services.events.hashing import event_hash
from api.services.events.sources.base import RawEvent


class EventDeduplicator:
    """First-seen-wins on (type, region, minute). Order = collection order."""

    def dedupe(self, events: list[RawEvent]) -> list[tuple[str, RawEvent]]:
        seen: dict[str, RawEvent] = {}
        for e in events:
            h = event_hash(e)
            if h not in seen:
                seen[h] = e
        return list(seen.items())
