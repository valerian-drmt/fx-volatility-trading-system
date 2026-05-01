"""Remaining placeholder sources from the spec — implement separately.

After §4 implementation pass : ECB / BoE / FOMC / Eurostat / ONS are now
real sources (modules in this package). BLS is the last stub (Tier 2,
intentionally disabled as of 2026-04 per spec §4 — activate only if FRED
shows drift over 30 days in production).
"""
from __future__ import annotations

from api.orchestration.events.sources.base import EventSource, RawEvent


class BLSSource(EventSource):
    """Tier 2 backup for FRED. Disabled by default."""

    name = "BLS"
    timeout_seconds = 10.0
    expected_min_events = 1

    async def fetch(self) -> list[RawEvent]:
        raise NotImplementedError(
            "BLSSource not implemented — Tier 2 fallback per spec §4. "
            "Activate only if FRED drifts in production."
        )
