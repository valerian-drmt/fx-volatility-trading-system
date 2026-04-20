"""Async database writer — consumes events from an in-process queue and
bulk-inserts them into PostgreSQL.

Scope of R2 PR #1 : queue + batch collection + bulk insert + table
dispatch. The following behaviors land in later R2 PRs, intentionally
NOT implemented here :

    - ON CONFLICT DO NOTHING idempotency   (R2 PR #2)
    - Exponential retries on OperationalError / InterfaceError  (R2 PR #2)
    - Graceful shutdown with queue drain   (R2 PR #2)
    - Controller integration + thread wiring  (R2 PR #3)
    - Engine producers (Market Data / Vol / Risk / Order)  (R2 PR #4)

Reference : releases/architecture_finale_project/07-async-db-writer.md
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from persistence.models import (
    AccountSnap,
    Base,
    Position,
    PositionSnapshot,
    Signal,
    Trade,
    VolSurface,
)

logger = logging.getLogger(__name__)


BATCH_SIZE_MAX: int = 100
BATCH_TIMEOUT_S: float = 5.0
QUEUE_MAX_SIZE: int = 10_000

# Dispatcher : event table_name -> ORM model class.
# The writer rejects any event targeting a table not in this map.
TABLE_MODELS: dict[str, type[Base]] = {
    "account_snaps": AccountSnap,
    "positions": Position,
    "position_snapshots": PositionSnapshot,
    "signals": Signal,
    "trades": Trade,
    "vol_surfaces": VolSurface,
}


Event = tuple[str, dict]


class AsyncDatabaseWriter:
    """Consume ``(table_name, payload)`` events and bulk-insert them.

    Two construction modes :

    1. Production : pass a ``database_url`` — the writer creates its own
       async engine and sessionmaker.
    2. Tests : pass a pre-built ``session_factory`` (typically bound to
       an aiosqlite engine). ``database_url`` is then ignored and
       ``engine`` stays ``None``.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        queue_max_size: int = QUEUE_MAX_SIZE,
        batch_size_max: int = BATCH_SIZE_MAX,
        batch_timeout_s: float = BATCH_TIMEOUT_S,
    ) -> None:
        if session_factory is None:
            if not database_url:
                raise ValueError(
                    "AsyncDatabaseWriter requires either a database_url or a "
                    "session_factory."
                )
            self.engine: AsyncEngine | None = create_async_engine(
                database_url,
                pool_pre_ping=True,
                future=True,
            )
            self.session_factory = async_sessionmaker(
                self.engine, expire_on_commit=False, class_=AsyncSession
            )
        else:
            self.engine = None
            self.session_factory = session_factory

        self.batch_size_max = batch_size_max
        self.batch_timeout_s = batch_timeout_s
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=queue_max_size)
        self.stop_event = asyncio.Event()

    async def run(self) -> None:
        """Main loop : drain the queue and bulk-insert until stop_event is set."""
        logger.info("AsyncDatabaseWriter started")
        while not self.stop_event.is_set():
            try:
                batch = await self._collect_batch()
                if not batch:
                    continue
                await self._write_batch(batch)
            except Exception:
                # Caught broadly on purpose : a single bad batch must never
                # kill the writer loop. Retries and DLQ land in R2 PR #2.
                logger.exception("writer loop error, batch dropped")
                await asyncio.sleep(1)
        logger.info("AsyncDatabaseWriter stopped")

    async def _collect_batch(self) -> list[Event]:
        """Pop up to ``batch_size_max`` events, or return early on timeout.

        Returns as soon as :
            - the batch is full, OR
            - ``batch_timeout_s`` elapsed since the call started.

        An empty list is a legitimate result (queue idle), the caller
        should skip the write in that case.
        """
        batch: list[Event] = []
        deadline = time.monotonic() + self.batch_timeout_s

        while len(batch) < self.batch_size_max:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=remaining)
            except TimeoutError:
                break
            batch.append(event)

        return batch

    async def _write_batch(self, batch: list[Event]) -> None:
        """Group events by target table and emit one bulk INSERT per table.

        Events referencing a table not in ``TABLE_MODELS`` are logged and
        skipped — the whole batch still commits so a single bad event does
        not take down valid peers.
        """
        by_table: dict[str, list[dict]] = defaultdict(list)
        for table_name, payload in batch:
            if table_name not in TABLE_MODELS:
                logger.warning("unknown table %r, dropping event", table_name)
                continue
            by_table[table_name].append(payload)

        if not by_table:
            return

        async with self.session_factory() as session:
            for table_name, rows in by_table.items():
                model = TABLE_MODELS[table_name]
                await session.execute(insert(model), rows)
            await session.commit()

        logger.debug(
            "wrote batch: %s",
            {k: len(v) for k, v in by_table.items()},
        )
