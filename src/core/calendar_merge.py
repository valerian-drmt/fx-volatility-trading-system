"""One economic calendar from two stores (#307, D2).

The hardcoded and computed calendars (ECB, BoE, FOMC, Eurostat, ONS) are
rows in Postgres; the FRED release dates are a list cached in Redis for 24
hours, because the FRED terms do not allow keeping them in a database. Every
reader merges the two here, so the api and the vol engine see one calendar.

An event is a mapping with ``event_type``, ``impact``, ``region``,
``scheduled_at`` (timezone-aware), ``description``, ``source`` and, for a
database row, ``id``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any


def _aware(e: Mapping[str, Any]) -> dict[str, Any]:
    """A copy whose time carries a zone (UTC when the driver dropped it)."""
    row = dict(e)
    if row["scheduled_at"].tzinfo is None:
        row["scheduled_at"] = row["scheduled_at"].replace(tzinfo=UTC)
    return row


def _identity(e: Mapping[str, Any]) -> tuple[Any, Any, str]:
    """Same rule as the pipeline's ``event_hash``: type, region, minute."""
    return (e.get("event_type"), e.get("region"),
            e["scheduled_at"].replace(second=0, microsecond=0).isoformat())


def merge_events(
    db_events: Iterable[Mapping[str, Any]], cached_events: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Both lists as one, ordered by time. A database row wins over a cached
    event with the same identity (an FOMC meeting is hardcoded and can also
    appear in FRED's release list)."""
    seen: dict[tuple[Any, Any, str], dict[str, Any]] = {}
    for e in db_events:
        row = _aware(e)
        seen.setdefault(_identity(row), row)
    for e in cached_events:
        row = {"id": None, **_aware(e)}
        seen.setdefault(_identity(row), row)
    return sorted(seen.values(), key=lambda e: e["scheduled_at"])


def next_event(
    events: Iterable[Mapping[str, Any]], now: datetime, *,
    impact: str | None = None, regions: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """The first event after ``now`` matching the filters, or None."""
    best: Mapping[str, Any] | None = None
    for e in events:
        if e["scheduled_at"] <= now:
            continue
        if impact is not None and e.get("impact") != impact:
            continue
        if regions is not None and e.get("region") not in regions:
            continue
        if best is None or e["scheduled_at"] < best["scheduled_at"]:
            best = e
    return dict(best) if best is not None else None
