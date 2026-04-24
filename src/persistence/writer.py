"""Async database writer — consumes events from an in-process queue and
bulk-inserts them into PostgreSQL.

Scope delivered so far :
    - R2 PR #1 : queue, batch collection, table dispatch, bulk INSERT, run()
    - R2 PR #2 : ON CONFLICT DO NOTHING on vol_surfaces / signals, exponential
                 retries on transient DB errors, graceful shutdown that drains
                 the queue and disposes the engine

Not yet implemented :
    - R2 PR #3 : Controller integration + thread wiring via run_coroutine_threadsafe
    - R2 PR #4 : Engine producers (MarketData, Vol, Risk, Order)
    - R2 PR #5 : CI postgres service + live integration tests

Reference : releases/architecture_finale_project/07-async-db-writer.md
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from sqlalchemy import Insert, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import InterfaceError, OperationalError
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
RETRY_ATTEMPTS: int = 3
SHUTDOWN_TIMEOUT_S: float = 30.0

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

# Tables where duplicates on the natural key are safe to drop silently.
# vol_surfaces and signals both have a UNIQUE constraint (see models.py),
# and the engines can re-emit the same (timestamp, underlying[, tenor]) on
# a retry : we want the first write to win and the second to be a no-op.
# Other tables (trades, position_snapshots, account_snaps) have no natural
# dedup key, duplicates there are real data and must not be silenced.
IDEMPOTENT_TABLES: dict[str, list[str]] = {
    "vol_surfaces": ["timestamp", "underlying"],
    "signals": ["timestamp", "underlying", "tenor"],
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
        retry_attempts: int = RETRY_ATTEMPTS,
        shutdown_timeout_s: float = SHUTDOWN_TIMEOUT_S,
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
        self.retry_attempts = retry_attempts
        self.shutdown_timeout_s = shutdown_timeout_s
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
                await self._write_batch_with_retries(batch)
            except Exception:
                # Last-resort catch : retries above already handle transient DB
                # errors. Anything reaching here is either a programming bug or
                # a non-recoverable error — log with stack and keep the loop
                # alive so other batches still flow.
                logger.exception("writer loop error, batch dropped")
                await asyncio.sleep(1)
        logger.info("AsyncDatabaseWriter stopped")

    async def shutdown(self) -> None:
        """Graceful shutdown : stop the loop, drain the queue, dispose the engine.

        Must be called from the same event loop the writer is running on
        (typically during application SIGTERM handling). Safe to call even
        if ``run()`` was never started — in that case only the engine is
        disposed and the queue is drained best-effort.
        """
        logger.info("AsyncDatabaseWriter shutdown initiated")
        self.stop_event.set()

        deadline = time.monotonic() + self.shutdown_timeout_s
        remaining: list[Event] = []
        while not self.queue.empty() and time.monotonic() < deadline:
            try:
                remaining.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if remaining:
            logger.info("draining %d remaining events on shutdown", len(remaining))
            # Chunk by batch_size_max so a large tail (say 8000 events) does
            # not explode into one oversized INSERT.
            for i in range(0, len(remaining), self.batch_size_max):
                chunk = remaining[i : i + self.batch_size_max]
                try:
                    await self._write_batch_with_retries(chunk)
                except Exception:
                    logger.exception(
                        "failed to drain chunk of %d events on shutdown", len(chunk)
                    )

        if self.engine is not None:
            await self.engine.dispose()
        logger.info("AsyncDatabaseWriter shutdown complete")

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

    async def _write_batch_with_retries(self, batch: list[Event]) -> None:
        """Wrap ``_write_batch`` with exponential backoff on transient errors.

        Only OperationalError / InterfaceError are retried — they signal a
        transient DB problem (connection reset, pool timeout, serialization
        failure) where retrying on the same batch has a realistic chance to
        succeed. Any other exception is re-raised immediately : it's either
        a programming bug (let it bubble up) or a constraint violation
        (retrying won't help, the row will still conflict).

        Sleep is ``2 ** attempt`` seconds : 1s, 2s, 4s — capped by
        ``retry_attempts``. After exhaustion the last exception is re-raised
        and the ``run()`` loop catches it.
        """
        last_exc: BaseException | None = None
        for attempt in range(self.retry_attempts):
            try:
                await self._write_batch(batch)
                if attempt > 0:
                    logger.info("batch written after %d retries", attempt)
                return
            except (OperationalError, InterfaceError) as e:
                last_exc = e
                if attempt + 1 >= self.retry_attempts:
                    break
                wait = 2**attempt
                logger.warning(
                    "transient DB error, retry %d/%d in %ds: %s",
                    attempt + 1,
                    self.retry_attempts,
                    wait,
                    e,
                )
                await asyncio.sleep(wait)
        logger.error(
            "batch write exhausted %d attempts, giving up: %s",
            self.retry_attempts,
            last_exc,
        )
        if last_exc is not None:
            raise last_exc

    async def _write_batch(self, batch: list[Event]) -> None:
        """Group events by target table and emit one bulk INSERT per table.

        Events referencing a table not in ``TABLE_MODELS`` are logged and
        skipped — the whole batch still commits so a single bad event does
        not take down valid peers. For tables in ``IDEMPOTENT_TABLES`` the
        INSERT is augmented with ``ON CONFLICT DO NOTHING`` when the engine
        is PostgreSQL, so re-emitting the same natural key is a no-op.
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
            dialect_name = session.bind.dialect.name
            for table_name, rows in by_table.items():
                stmt = self._build_insert(table_name, rows, dialect_name)
                await session.execute(stmt)
            await session.commit()

        logger.debug(
            "wrote batch: %s",
            {k: len(v) for k, v in by_table.items()},
        )

    def _build_insert(
        self, table_name: str, rows: list[dict], dialect_name: str
    ) -> Insert:
        """Return the INSERT statement to use for ``table_name`` under ``dialect_name``.

        On PostgreSQL, tables in ``IDEMPOTENT_TABLES`` get
        ``ON CONFLICT (<key cols>) DO NOTHING`` to silently drop duplicates
        on the natural key. On any other dialect (notably sqlite in tests)
        we emit a plain bulk INSERT — tests never feed duplicates, and the
        sqlite idempotency story is different enough that it is not worth
        emulating just for unit tests.
        """
        model = TABLE_MODELS[table_name]
        if dialect_name == "postgresql" and table_name in IDEMPOTENT_TABLES:
            pg_stmt = pg_insert(model).values(rows)
            return pg_stmt.on_conflict_do_nothing(
                index_elements=IDEMPOTENT_TABLES[table_name]
            )
        return insert(model).values(rows)
