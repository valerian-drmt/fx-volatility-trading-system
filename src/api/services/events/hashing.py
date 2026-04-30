"""SHA-256 truncated to 16 hex chars — identity = (event_type, region, minute).

Pourquoi tronquer à la minute (cf. spec §3) : 2 sources peuvent renvoyer
14:30:00 vs 14:30:15 pour le même release, la seconde n'a pas de valeur
sémantique (BC publient à la minute près). Truncation = no false duplicates.
"""
from __future__ import annotations

import hashlib

from api.services.events.sources.base import RawEvent


def event_hash(e: RawEvent) -> str:
    """Stable identity hash for an event row. 16 hex chars (~64 bits)."""
    minute = e.scheduled_at.replace(second=0, microsecond=0).isoformat()
    key = f"{e.event_type}|{e.region}|{minute}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
