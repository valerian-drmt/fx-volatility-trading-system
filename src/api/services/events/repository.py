"""Idempotent INSERT for events. Cf. spec §3 + migration 012."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.events.sources.base import RawEvent
from persistence.models import Event

SessionFactory = Callable[[], Awaitable[AsyncSession]]


class EventsRepository:
    """Bulk-INSERT events with ``ON CONFLICT (event_hash) DO NOTHING``.

    The session_factory must yield an :class:`AsyncSession`. Used pattern :
        repo = EventsRepository(get_sessionmaker())
        await repo.upsert_many(hashed_events)
    """

    def __init__(self, sessionmaker: Callable[[], AsyncSession]):
        self._sessionmaker = sessionmaker

    async def upsert_many(
        self, hashed_events: list[tuple[str, RawEvent]],
    ) -> int:
        """Insert ``[(hash, event)]`` rows. Returns rows actually inserted
        (i.e. excludes ON CONFLICT skips).
        """
        if not hashed_events:
            return 0

        rows = [
            {
                "event_hash": h,
                "event_type": e.event_type,
                "region": e.region,
                "impact": e.impact,
                "scheduled_at": e.scheduled_at,
                "description": e.description,
                "source": e.source_name,
            }
            for h, e in hashed_events
        ]

        async with self._sessionmaker() as session:
            stmt = pg_insert(Event).values(rows).on_conflict_do_nothing(
                index_elements=["event_hash"],
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0
