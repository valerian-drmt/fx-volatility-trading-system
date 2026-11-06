"""The ``account_snapshot`` cache key (#366 / #368).

The execution engine publishes every account snapshot on the ``account``
channel and mirrors it under ``keys.ACCOUNT_SNAPSHOT`` (TTL 60 s). The header
endpoint reads its live figures from here instead of the ``account_history``
table; a missing key (engine down, cold start) reads as ``None`` and the
caller falls back to one indexed DB row.
"""
from __future__ import annotations

import json
from typing import Any

from bus import keys


async def read_account_snapshot(redis: Any) -> dict[str, Any] | None:
    """The ``latest`` row of the cached ``account_snapshot`` message, or None."""
    if redis is None:
        return None
    try:
        raw = await redis.get(keys.ACCOUNT_SNAPSHOT)
    except Exception:
        return None
    if not raw:
        return None
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(msg, dict):
        return None
    latest = msg.get("latest")
    if isinstance(latest, dict):
        return latest
    # Pre-#366 shape: the payload itself was the row.
    return msg if "net_liq_usd" in msg else None
