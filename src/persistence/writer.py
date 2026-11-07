"""``AsyncDatabaseWriter`` — bulk-INSERT engine for PostgreSQL.

Reusable infrastructure : a process-local ``asyncio.Queue`` of
``(table, row_dict)`` items, a batched flush loop with idempotency
(ON CONFLICT DO NOTHING on tables with a UNIQUE key) and exponential
retries on transient DB errors. Disposing drains the queue and closes
the engine cleanly.

Composition site : ``src/engines/db_writer/service.py`` wraps an
instance with a Redis pub/sub subscriber loop (the ``DbWriterService``)
to make the long-running container ; manual smoke scripts use the writer
directly without Redis. Both code paths exercise the same batching
core.

Reference : releases/architecture_finale_project/07-async-db-writer.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import Insert, insert
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from persistence.models import (
    AccountHistory,
    Base,
    DbWriterDeadLetter,
    FeatureHistory,
    OpenPosition,
    OpenPositionHistory,
    PcaSignal,
    RegimeSnapshot,
    SurfaceSnapshotHourly,
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
    "account_history": AccountHistory,                        # renamed in migration 026 (Theme 2)
    "feature_history": FeatureHistory,                        # renamed in migration 023
    "pca_signal_history": PcaSignal,                          # renamed in migration 023
    "open_position": OpenPosition,                                # renamed in migration 033
    "open_position_history": OpenPositionHistory,           # renamed in migration 033
    "regime_snapshot_history": RegimeSnapshot,                # renamed in migration 040
    "pca_surface_snapshot_history": SurfaceSnapshotHourly,    # renamed in migration 036
    "vol_surface_history": VolSurface,                        # renamed in migration 023
}

# Tables where duplicates on the natural key are safe to drop silently
# (``ON CONFLICT DO NOTHING``). Every table an engine streams through the
# db-writer is here (#324, DI-02): the vol engine stamps ONE ``cycle_ts`` on
# all the rows of a cycle, so a Redis redelivery or a writer retry after an
# in-doubt commit re-sends the same natural key and is a no-op. Before this
# the producer stamped ``datetime.now()`` at publish time, so only a
# byte-identical frame could ever collide and the keys were decorative.
# `open_position` uses the primary key ``id`` (a deterministic hash of the IB
# composite key) : re-observing the same open position must be idempotent.
# ``account_history`` / ``open_position_history`` are written by their
# engines directly, not through this writer.
IDEMPOTENT_TABLES: dict[str, list[str]] = {
    "vol_surface_history": ["timestamp", "underlying"],
    "open_position": ["id"],
    "feature_history": ["symbol", "timestamp"],
    "regime_snapshot_history": ["symbol", "timestamp"],                                  # migration 059
    "pca_surface_snapshot_history": ["symbol", "timestamp"],
    "pca_signal_history": ["symbol", "timestamp", "pca_model_id", "pc_id"],
}

# Errors that mean "this row (or batch) will never insert": fall back to
# per-row writes and dead-letter what still fails. Transient errors
# (OperationalError / InterfaceError) are NOT here: they go to the retry loop.
_ROW_ERRORS: tuple[type[BaseException], ...] = (
    sa_exc.IntegrityError, sa_exc.DataError, sa_exc.ProgrammingError, sa_exc.StatementError,
    ValueError, TypeError,
)


def _metric(name: str) -> Any:
    try:
        from shared import observability

        return getattr(observability, name)
    except Exception:  # metrics are never load-bearing
        return None


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
        """Group events by target table and emit one bulk INSERT per table,
        each table in its own transaction.

        Events referencing a table not in ``TABLE_MODELS`` go to the dead
        letter. For tables in ``IDEMPOTENT_TABLES`` the INSERT carries
        ``ON CONFLICT DO NOTHING`` on the natural key (PostgreSQL and SQLite),
        so re-emitting the same key is a no-op. A bulk insert that fails on a
        row error (constraint, type) is retried row by row and the rows that
        still fail are parked in ``db_writer_dead_letter`` with the error
        (#324, DI-04); the valid peers land. Transient errors propagate to the
        retry loop, which re-sends the whole batch, now idempotently.
        """
        by_table: dict[str, list[dict]] = defaultdict(list)
        for table_name, payload in batch:
            if table_name not in TABLE_MODELS:
                logger.warning("unknown table %r, dead-lettering event", table_name)
                await self._dead_letter(table_name, payload, "unknown table")
                continue
            by_table[table_name].append(payload)

        if not by_table:
            return

        written: dict[str, int] = {}
        for table_name, rows in by_table.items():
            try:
                async with self.session_factory() as session:
                    dialect_name = session.bind.dialect.name
                    await session.execute(self._build_insert(table_name, rows, dialect_name))
                    await session.commit()
                written[table_name] = len(rows)
            except (OperationalError, InterfaceError):
                raise  # transient: the retry loop re-sends the whole batch
            except _ROW_ERRORS as exc:
                logger.warning(
                    "bulk insert into %s failed (%s); falling back to per-row writes",
                    table_name, type(exc).__name__,
                )
                written[table_name] = await self._write_rows_one_by_one(table_name, rows)
            m = _metric("db_writer_rows_written_total")
            if m is not None and written.get(table_name):
                m.labels(table=table_name).inc(written[table_name])

        logger.debug("wrote batch: %s", written)

    async def _write_rows_one_by_one(self, table_name: str, rows: list[dict]) -> int:
        """Per-row fallback: each row in its own transaction; a row that still
        fails on a row error is dead-lettered. Returns the rows written."""
        ok = 0
        for row in rows:
            try:
                async with self.session_factory() as session:
                    dialect_name = session.bind.dialect.name
                    await session.execute(self._build_insert(table_name, [row], dialect_name))
                    await session.commit()
                ok += 1
            except (OperationalError, InterfaceError):
                raise
            except _ROW_ERRORS as exc:
                await self._dead_letter(table_name, row, f"{type(exc).__name__}: {exc}")
        return ok

    async def _dead_letter(self, table_name: str, payload: dict, error: str) -> None:
        """Park one row with its error. Best effort: if even this fails the
        row is logged in full and the counter still moves."""
        m = _metric("db_writer_dead_letter_total")
        if m is not None:
            m.labels(table=table_name).inc()
        try:
            async with self.session_factory() as session:
                session.add(DbWriterDeadLetter(
                    table_name=str(table_name)[:64], payload=_jsonable(payload), error=str(error)[:2000],
                ))
                await session.commit()
            logger.error("db_writer_dead_letter table=%s error=%s", table_name, str(error)[:200])
        except Exception:
            logger.exception("db_writer_dead_letter_write_failed table=%s payload=%r", table_name, payload)

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
        if table_name in IDEMPOTENT_TABLES:
            keys = IDEMPOTENT_TABLES[table_name]
            if dialect_name == "postgresql":
                return pg_insert(model).values(rows).on_conflict_do_nothing(index_elements=keys)
            if dialect_name == "sqlite":
                return sqlite_insert(model).values(rows).on_conflict_do_nothing(index_elements=keys)
        return insert(model).values(rows)


def _json_default(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return str(v)


def _jsonable(payload: dict) -> dict:
    """Datetimes (coerced by the service), Decimals and anything else the
    JSON column cannot take, made serialisable; nested values included."""
    return json.loads(json.dumps(payload, default=_json_default))
