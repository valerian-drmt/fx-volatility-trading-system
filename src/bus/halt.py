"""Redis mirror of the kill switch (#315): a JSON last value for the UI and
the ``system_alerts`` publish. Postgres (``persistence.halt``) is the
authority; this is the fast, best-effort view."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from bus import channels

KEY = "trading:halted"


async def mirror_halt(redis: Redis, *, halted: bool, reason: str, by: str) -> None:
    body = {"halted": halted, "reason": reason, "by": by, "at": datetime.now(UTC).isoformat()}
    raw = json.dumps(body)
    await redis.set(KEY, raw)
    await redis.publish(channels.CH_SYSTEM_ALERTS, json.dumps({
        "kind": "trading_halted" if halted else "trading_resumed",
        "severity": "critical" if halted else "info",
        **body,
    }))


async def read_halt_mirror(redis: Redis | None) -> dict[str, Any] | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
