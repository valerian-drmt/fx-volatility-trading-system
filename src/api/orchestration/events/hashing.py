"""SHA-256 truncated to 16 hex chars — identity = (event_type, region, minute).

Why truncate to the minute (cf. spec §3): 2 sources can return
14:30:00 vs 14:30:15 for the same release; the seconds carry no semantic
value (central banks publish to the minute). Truncation = no false duplicates.
"""
from __future__ import annotations

import hashlib

from api.orchestration.events.sources.base import RawEvent


def event_hash(e: RawEvent) -> str:
    """Stable identity hash for an event row. 16 hex chars (~64 bits)."""
    minute = e.scheduled_at.replace(second=0, microsecond=0).isoformat()
    key = f"{e.event_type}|{e.region}|{minute}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
