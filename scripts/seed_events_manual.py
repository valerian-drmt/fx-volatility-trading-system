"""Seed the events table with a hardcoded list of high-impact events.

Fallback for when ForexFactory feed is down or for offline/sandbox bootstrap.
Maintained manually : edit the EVENTS list below once a quarter (~5 min).

Usage :
    docker exec -it fxvol-api python scripts/seed_events_manual.py

Source : ECB monetary calendar + Federal Reserve FOMC calendar +
BLS releases (NFP/CPI). Update when new dates are published.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import select

from persistence.db import get_sessionmaker
from persistence.models import Event

# Format : (event_type, region, ISO datetime UTC, description)
# 2026 Q2 / Q3 — high-impact rate decisions + key macro releases.
EVENTS: list[tuple[str, str, str, str]] = [
    # ECB rate decisions (Thursdays, 12:15 UTC press release + 12:45 conf)
    ("ECB", "EU", "2026-05-08T12:15:00+00:00", "ECB Main Refinancing Rate decision + press conference"),
    ("ECB", "EU", "2026-06-19T12:15:00+00:00", "ECB Main Refinancing Rate decision"),
    ("ECB", "EU", "2026-07-31T12:15:00+00:00", "ECB Main Refinancing Rate decision"),
    # FOMC (Wednesdays, 18:00 UTC statement + 18:30 conf)
    ("FOMC", "US", "2026-05-14T18:00:00+00:00", "FOMC rate decision + press conference"),
    ("FOMC", "US", "2026-06-25T18:00:00+00:00", "FOMC rate decision"),
    ("FOMC", "US", "2026-07-30T18:00:00+00:00", "FOMC rate decision"),
    # NFP — 1st Friday of each month, 12:30 UTC
    ("NFP", "US", "2026-05-02T12:30:00+00:00", "Non-Farm Payrolls"),
    ("NFP", "US", "2026-06-06T12:30:00+00:00", "Non-Farm Payrolls"),
    ("NFP", "US", "2026-07-04T12:30:00+00:00", "Non-Farm Payrolls"),
    # CPI US — mid-month, 12:30 UTC
    ("CPI_US", "US", "2026-05-13T12:30:00+00:00", "US CPI YoY"),
    ("CPI_US", "US", "2026-06-11T12:30:00+00:00", "US CPI YoY"),
    ("CPI_US", "US", "2026-07-15T12:30:00+00:00", "US CPI YoY"),
    # CPI EU
    ("CPI_EU", "EU", "2026-05-16T09:00:00+00:00", "Eurozone CPI YoY"),
    ("CPI_EU", "EU", "2026-06-18T09:00:00+00:00", "Eurozone CPI YoY"),
    # GDP US (advance Q1 release)
    ("GDP_US", "US", "2026-05-29T12:30:00+00:00", "US GDP QoQ Advance"),
    # BoE
    ("BOE", "GB", "2026-05-08T11:00:00+00:00", "BoE Bank Rate decision"),
    ("BOE", "GB", "2026-06-19T11:00:00+00:00", "BoE Bank Rate decision"),
]


async def main() -> None:
    async with get_sessionmaker()() as session:
        inserted = 0
        skipped = 0
        for event_type, region, scheduled_iso, description in EVENTS:
            scheduled_at = datetime.fromisoformat(scheduled_iso)
            existing = (await session.execute(
                select(Event)
                .where(Event.event_type == event_type)
                .where(Event.scheduled_at == scheduled_at)
                .limit(1)
            )).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue
            session.add(Event(
                event_type=event_type, impact="high", region=region,
                scheduled_at=scheduled_at, description=description,
                source="manual_seed",
            ))
            inserted += 1
        await session.commit()
    print(f"events seeded — inserted={inserted}, skipped_duplicates={skipped}, total_in_list={len(EVENTS)}")
    now = datetime.now(UTC)
    upcoming = sum(1 for _, _, ts, _ in EVENTS if datetime.fromisoformat(ts) > now)
    print(f"upcoming events (after {now.isoformat()}): {upcoming}")


if __name__ == "__main__":
    asyncio.run(main())
