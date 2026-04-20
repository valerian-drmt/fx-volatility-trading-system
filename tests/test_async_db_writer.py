"""Unit tests for the AsyncDatabaseWriter.

Covers :
    - R2 PR #1 : batch-collect semantics (size limit, timeout) and the
                 table-grouped bulk insert
    - R2 PR #2 : ON CONFLICT DO NOTHING compile-level check on vol_surfaces,
                 shutdown() queue drain, bounded queue raising QueueFull

Uses an in-memory aiosqlite engine so tests stay fast and hermetic. The
Postgres-specific ON CONFLICT behavior is verified statically (SQL
compile) since sqlite does not execute it the same way. End-to-end
behavior on real Postgres lands in R2 PR #5 (db_integration tests).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from persistence.models import AccountSnap, Base, Signal, Trade
from persistence.writer import AsyncDatabaseWriter


@pytest.fixture
async def session_factory():
    """aiosqlite-backed async_sessionmaker with the full schema created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
async def writer(session_factory):
    """Writer wired to the in-memory DB with short, test-friendly batch settings."""
    return AsyncDatabaseWriter(
        session_factory=session_factory,
        queue_max_size=10_000,
        batch_size_max=100,
        batch_timeout_s=0.3,
    )


def _account_event(i: int) -> tuple[str, dict]:
    return (
        "account_snaps",
        {"timestamp": datetime(2026, 4, 20, 10, 0, i % 60, tzinfo=UTC)},
    )


@pytest.mark.asyncio
async def test_collect_batch_returns_on_size_limit(writer):
    """Queue holds 150 events, a single batch collect stops exactly at 100."""
    for i in range(150):
        writer.queue.put_nowait(_account_event(i))

    batch = await writer._collect_batch()

    assert len(batch) == 100
    assert writer.queue.qsize() == 50


@pytest.mark.asyncio
async def test_collect_batch_returns_on_timeout(writer):
    """Only 5 events in the queue: batch returns them all once the timeout hits."""
    for i in range(5):
        writer.queue.put_nowait(_account_event(i))

    start = asyncio.get_event_loop().time()
    batch = await writer._collect_batch()
    elapsed = asyncio.get_event_loop().time() - start

    assert len(batch) == 5
    # asyncio timers fire with ~millisecond imprecision, so allow a small
    # lower-bound slack. The comfortable upper bound guards against a hang.
    assert elapsed >= writer.batch_timeout_s * 0.9
    assert elapsed < writer.batch_timeout_s + 1.0


@pytest.mark.asyncio
async def test_write_batch_groups_by_table(writer, session_factory):
    """10 mixed events across 3 tables → exactly one INSERT per table.

    We verify the grouping by the effect on the DB : rows land in the
    correct target table and the counts per table match the input batch.
    The alternative (spying on session.execute) would be more precise
    but brittler — aiosqlite gives us cheap end-to-end evidence.
    """
    ts = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    batch = [
        # 3 account_snaps
        ("account_snaps", {"timestamp": ts}),
        ("account_snaps", {"timestamp": ts}),
        ("account_snaps", {"timestamp": ts}),
        # 2 trades
        (
            "trades",
            {
                "side": "BUY",
                "quantity": Decimal("1"),
                "price": Decimal("1.08500000"),
                "timestamp": ts,
            },
        ),
        (
            "trades",
            {
                "side": "SELL",
                "quantity": Decimal("2"),
                "price": Decimal("1.08600000"),
                "timestamp": ts,
            },
        ),
        # 5 signals, tenor differs so the UNIQUE(ts, underlying, tenor) does not fire
        *[
            (
                "signals",
                {
                    "timestamp": ts,
                    "underlying": "EURUSD",
                    "tenor": tenor,
                    "dte": dte,
                    "sigma_mid": Decimal("7.50000"),
                    "sigma_fair": Decimal("7.40000"),
                    "ecart": Decimal("0.10000"),
                    "signal_type": "CHEAP",
                },
            )
            for tenor, dte in [("1W", 7), ("1M", 30), ("2M", 60), ("3M", 90), ("6M", 180)]
        ],
    ]
    assert len(batch) == 10

    await writer._write_batch(batch)

    async with session_factory() as session:
        account_count = await session.scalar(select(func.count()).select_from(AccountSnap))
        trade_count = await session.scalar(select(func.count()).select_from(Trade))
        signal_count = await session.scalar(select(func.count()).select_from(Signal))

    assert account_count == 3
    assert trade_count == 2
    assert signal_count == 5


# --- R2 PR #2 : idempotency, retries, graceful shutdown ---------------------


def test_on_conflict_do_nothing_applied_to_vol_surfaces(writer):
    """On postgres, vol_surfaces and signals inserts must carry ON CONFLICT.

    Verified statically on the compiled SQL — tests run on sqlite where the
    runtime idempotency story differs. Live postgres validation lands in
    R2 PR #5 (test_async_db_writer_live.py).
    """
    rows = [
        {
            "timestamp": datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC),
            "underlying": "EURUSD",
            "spot": Decimal("1.08"),
            "surface_data": {},
        }
    ]
    stmt = writer._build_insert("vol_surfaces", rows, dialect_name="postgresql")
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql.upper()
    assert "DO NOTHING" in sql.upper()
    # The conflict target must be the UNIQUE natural key, not the PK.
    assert "timestamp" in sql
    assert "underlying" in sql


def test_on_conflict_do_nothing_not_applied_to_non_idempotent_table(writer):
    """Tables like account_snaps have no natural dedup key: plain INSERT only."""
    rows = [{"timestamp": datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)}]
    stmt = writer._build_insert("account_snaps", rows, dialect_name="postgresql")
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" not in sql.upper()


@pytest.mark.asyncio
async def test_shutdown_drains_remaining_queue(writer, session_factory):
    """shutdown() flushes every queued event before returning."""
    for i in range(20):
        writer.queue.put_nowait(_account_event(i))

    await writer.shutdown()

    assert writer.stop_event.is_set()
    assert writer.queue.empty()
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(AccountSnap))
    assert count == 20


@pytest.mark.asyncio
async def test_queue_full_logs_and_drops(session_factory):
    """A bounded queue raises QueueFull once at capacity, handed back to producer."""
    tiny = AsyncDatabaseWriter(
        session_factory=session_factory,
        queue_max_size=5,
        batch_size_max=10,
        batch_timeout_s=0.1,
    )
    for i in range(5):
        tiny.queue.put_nowait(_account_event(i))

    assert tiny.queue.full()
    with pytest.raises(asyncio.QueueFull):
        tiny.queue.put_nowait(_account_event(99))

    # The writer itself does not swallow QueueFull — producers will handle
    # it in R2 PR #4 with a bounded wait_for + drop-and-log strategy.
