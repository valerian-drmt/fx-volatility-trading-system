"""FRED release dates live in Redis for 24 hours, and nowhere else (#307, D2).

The FRED terms of use do not allow storing FRED content in a database. The
events pipeline used to write the release dates it fetched into Postgres
(``event_calendar``), from where they also went into the nightly backups.

They are now kept in one Redis key with a 24 hour expiry. The scheduler
refreshes it every 12 hours, so a healthy system always has it; when FRED
cannot be reached for a day the key expires and the US releases are simply
absent until the next successful fetch (the other calendars, hardcoded or
computed, are in Postgres and unaffected).

Both readers of the calendar, the api (macro-events panel, regime endpoints)
and the vol engine (regime gate), merge this list with the database rows
through ``core.calendar_merge``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

FRED_EVENTS_KEY = "events:fred"
FRED_EVENTS_TTL_S = 24 * 60 * 60
FRED_SOURCE = "FRED"
_FIELDS = ("event_type", "impact", "region", "scheduled_at", "description", "source")


async def store_fred_events(redis: Any, events: list[dict[str, Any]]) -> int:
    """Replace the cached list. ``scheduled_at`` is a timezone-aware datetime.
    Returns the number of events stored; 0 and nothing written for an empty
    list, so a failed fetch never erases a list that is still valid."""
    if redis is None or not events:
        return 0
    rows = [
        {**{k: e.get(k) for k in _FIELDS if k != "scheduled_at"},
         "scheduled_at": e["scheduled_at"].isoformat()}
        for e in events
    ]
    await redis.set(FRED_EVENTS_KEY, json.dumps(rows), ex=FRED_EVENTS_TTL_S)
    return len(rows)


async def load_fred_events(redis: Any) -> list[dict[str, Any]]:
    """The cached FRED events, ``[]`` when there is none or Redis cannot be
    read. Never raises: the calendar must still answer from the database."""
    if redis is None:
        return []
    try:
        raw = await redis.get(FRED_EVENTS_KEY)
        if not raw:
            return []
        out = []
        for row in json.loads(raw):
            out.append({**{k: row.get(k) for k in _FIELDS},
                        "scheduled_at": datetime.fromisoformat(row["scheduled_at"])})
        return out
    except Exception:
        logger.warning("fred_cache_unreadable", exc_info=True)
        return []
