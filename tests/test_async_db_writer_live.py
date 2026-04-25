"""End-to-end integration tests for the AsyncDatabaseWriter.

Runs the writer against a real PostgreSQL instance (same service the
alembic tests use in CI). Complements the unit suite in
tests/test_async_db_writer.py which runs on aiosqlite : unit suite
proves the batching and dispatch logic, this live suite proves the
Postgres-specific features (ON CONFLICT DO NOTHING, asyncpg driver,
JSONB round-trip) really work under the production dialect.

Gated by ``DB_RUN_INTEGRATION=1`` via conftest.py — skipped otherwise.
Locally :

    docker compose -f docker-compose.dev.yml up -d postgres
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
    $env:DB_RUN_INTEGRATION = "1"
    python scripts/db_apply.py
    python -m pytest tests/test_async_db_writer_live.py -v
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from persistence.writer import AsyncDatabaseWriter

pytestmark = pytest.mark.db_integration

# Tables we touch in this suite — truncated before/after each test so the
# assertions on exact row counts stay meaningful even when other live
# tests run before or after in the same CI job.
_TEST_TABLES = ("vol_surfaces", "signals")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "persistence" / "alembic.ini"


@pytest.fixture(autouse=True, scope="module")
def _ensure_schema():
    """Re-apply migrations before any test in this module runs.

    The sibling suite ``test_alembic_migrations.py`` drops the ``public``
    schema in its teardown to keep its own tests isolated. If it runs
    before us in the same job (pytest collects files alphabetically :
    test_alembic_migrations < test_async_db_writer_live), we arrive to
    an empty schema — ``alembic upgrade head`` puts it back.

    Idempotent : if the schema is already at head, this is a ~1s no-op.
    """
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    yield


def _sync_url() -> str:
    """Return the sync-driver URL used for verification SELECTs."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    # Writer uses asyncpg ; verify with psycopg2 (sync create_engine).
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture
def truncate_tables():
    """Empty the target tables before and after the test for isolation."""
    engine = create_engine(_sync_url(), future=True)
    stmt = text(f"TRUNCATE TABLE {', '.join(_TEST_TABLES)} RESTART IDENTITY CASCADE")
    with engine.begin() as conn:
        conn.execute(stmt)
    yield
    with engine.begin() as conn:
        conn.execute(stmt)
    engine.dispose()


async def _run_writer_with_events(events: list[tuple[str, dict]]) -> None:
    """Start a writer, push the events, drain via shutdown, dispose engine."""
    writer = AsyncDatabaseWriter(
        database_url=os.environ["DATABASE_URL"],
        batch_timeout_s=0.3,
    )
    run_task = asyncio.create_task(writer.run())
    for event in events:
        await writer.queue.put(event)
    await writer.shutdown()
    await run_task


def _count(table: str) -> int:
    engine = create_engine(_sync_url(), future=True)
    try:
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
    finally:
        engine.dispose()


def _vol_surface_row(
    *,
    timestamp: datetime,
    underlying: str = "EURUSD",
    spot: str = "1.0857",
    forward: str = "1.08600",
) -> dict:
    return {
        "timestamp": timestamp,
        "underlying": underlying,
        "spot": Decimal(spot),
        "forward": Decimal(forward),
        "surface_data": {"1M": {"iv": 7.5}, "3M": {"iv": 8.0}},
        "fair_vol_data": {"1M": 7.4, "3M": 7.9},
        "rv_data": {"1M": 7.6, "3M": 8.1},
    }


def _signal_row(tenor: str, dte: int, timestamp: datetime) -> dict:
    return {
        "timestamp": timestamp,
        "underlying": "EURUSD",
        "tenor": tenor,
        "dte": dte,
        "sigma_mid": Decimal("7.50"),
        "sigma_fair": Decimal("7.40"),
        "ecart": Decimal("0.10"),
        "signal_type": "CHEAP",
    }


@pytest.mark.asyncio
async def test_write_vol_surface_end_to_end(truncate_tables):
    """One vol_surface event → one row in Postgres, JSONB round-trip intact."""
    ts = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    await _run_writer_with_events([("vol_surfaces", _vol_surface_row(timestamp=ts))])

    engine = create_engine(_sync_url(), future=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT underlying, spot, forward, surface_data, fair_vol_data "
                    "FROM vol_surfaces WHERE timestamp = :ts"
                ),
                {"ts": ts},
            ).one()
    finally:
        engine.dispose()

    assert row.underlying == "EURUSD"
    assert row.spot == Decimal("1.0857")
    assert row.forward == Decimal("1.08600")
    # JSONB round-trips to a dict on read.
    assert row.surface_data == {"1M": {"iv": 7.5}, "3M": {"iv": 8.0}}
    assert row.fair_vol_data == {"1M": 7.4, "3M": 7.9}


@pytest.mark.asyncio
async def test_write_signals_batch(truncate_tables):
    """Six signals at the same timestamp with distinct tenors all land."""
    ts = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    events = [
        ("signals", _signal_row(tenor, dte, ts))
        for tenor, dte in [("1W", 7), ("1M", 30), ("2M", 60), ("3M", 90), ("6M", 180), ("1Y", 365)]
    ]
    await _run_writer_with_events(events)

    assert _count("signals") == 6

    engine = create_engine(_sync_url(), future=True)
    try:
        with engine.connect() as conn:
            tenors = {
                r[0]
                for r in conn.execute(
                    text("SELECT tenor FROM signals WHERE timestamp = :ts"),
                    {"ts": ts},
                )
            }
    finally:
        engine.dispose()
    assert tenors == {"1W", "1M", "2M", "3M", "6M", "1Y"}


@pytest.mark.asyncio
async def test_idempotency_on_duplicate_vol_surface(truncate_tables):
    """ON CONFLICT DO NOTHING makes duplicate (timestamp, underlying) a no-op.

    This is the key prod behavior we could not verify in the sqlite unit
    suite — the ON CONFLICT compile test checked the generated SQL, this
    one checks Postgres actually honours it at execution time.
    """
    ts = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
    row = _vol_surface_row(timestamp=ts)
    duplicate = _vol_surface_row(timestamp=ts, spot="9.9999")  # same key, different data

    await _run_writer_with_events([
        ("vol_surfaces", row),
        ("vol_surfaces", duplicate),
    ])

    assert _count("vol_surfaces") == 1

    engine = create_engine(_sync_url(), future=True)
    try:
        with engine.connect() as conn:
            spot = conn.execute(
                text("SELECT spot FROM vol_surfaces WHERE timestamp = :ts"),
                {"ts": ts},
            ).scalar_one()
    finally:
        engine.dispose()
    # First write wins : the "duplicate" with spot=9.9999 was silently dropped.
    assert spot == Decimal("1.0857")
