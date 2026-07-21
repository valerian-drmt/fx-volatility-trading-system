"""Exponential-backoff math, shared by every reconnect loop.

Extracted from ``shared.ib_connection`` so non-IB consumers (e.g. the
db-writer Redis subscriber) can reuse the same capped schedule without
pulling in the IB connection helpers. ``shared.ib_connection`` re-exports
these names for backwards compatibility.
"""
from __future__ import annotations

MIN_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0


def next_backoff_seconds(attempt: int) -> float:
    """Return the wait before attempt ``attempt`` (0-based).

    ``attempt=0`` → 1 s, ``attempt=1`` → 2 s, ``attempt=2`` → 4 s, ...
    capped at ``MAX_BACKOFF_S``. Negative attempts clamp to the minimum.
    """
    if attempt < 0:
        return MIN_BACKOFF_S
    delay = MIN_BACKOFF_S * (2 ** attempt)
    return min(delay, MAX_BACKOFF_S)
