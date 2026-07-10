"""db_integration tests for the Postgres-only SQL in ``api.routers.portfolio_panel``.

These exercise the ``equity_curve`` endpoint's raw SQL, which cannot run on the
aiosqlite unit-test DB: ``DISTINCT ON (bucket_ts)`` downsampling built on
``to_timestamp(floor(extract(epoch ...)))`` plus the end-of-day flagging.

Gated by the ``db_integration`` marker — needs a live PostgreSQL with the schema
applied (``alembic upgrade head``), ``DATABASE_URL`` set and ``DB_RUN_INTEGRATION=1``.
Each test runs inside a transaction that is rolled back, so nothing persists.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytest_asyncio = pytest.importorskip("pytest_asyncio")

pytestmark = [pytest.mark.db_integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def session():
    url = os.environ.get("DATABASE_URL")
    if not url or not os.environ.get("DB_RUN_INTEGRATION"):
        pytest.skip("db_integration: set DB_RUN_INTEGRATION=1 and DATABASE_URL")
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(url)
    conn = await engine.connect()
    trans = await conn.begin()
    db = AsyncSession(bind=conn)
    try:
        yield db
    finally:
        await db.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _snap(db, ts: datetime, net_liq: float) -> None:
    from persistence.models import AccountHistory

    db.add(AccountHistory(timestamp=ts, net_liq_usd=Decimal(str(net_liq))))
    await db.flush()


async def test_equity_curve_distinct_on_keeps_latest_per_bucket(session):
    """DISTINCT ON collapses each 60s bucket (window=1d) to its latest snap."""
    from api.routers.portfolio_panel import equity_curve

    base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    await _snap(session, base + timedelta(seconds=10), 100_000)
    await _snap(session, base + timedelta(seconds=40), 111_111)          # latest in bucket 0
    await _snap(session, base + timedelta(minutes=1, seconds=5), 222_222)  # bucket 1

    points = await equity_curve(session, window="1d")

    assert len(points) == 2
    assert [p["net_liq_usd"] for p in points] == [111_111.0, 222_222.0]
    assert (
        datetime.fromisoformat(points[0]["timestamp"])
        < datetime.fromisoformat(points[1]["timestamp"])
    )


async def test_equity_curve_excludes_rows_outside_window(session):
    """Rows older than the window's lookback are filtered out server-side."""
    from api.routers.portfolio_panel import equity_curve

    await _snap(session, datetime.now(UTC) - timedelta(days=10), 100_000)
    assert await equity_curve(session, window="1d") == []


async def test_equity_curve_flags_eod_only_before_22_utc(session):
    """EOD = the last bucket of a UTC day before 22:00; 22:00+ points are not EOD."""
    from api.routers.portfolio_panel import equity_curve

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    t21 = datetime(yesterday.year, yesterday.month, yesterday.day, 21, 0, tzinfo=UTC)
    t23 = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 0, tzinfo=UTC)
    await _snap(session, t21, 100_000)
    await _snap(session, t23, 200_000)

    is_eod = {p["net_liq_usd"]: p["is_eod"] for p in await equity_curve(session, window="30d")}

    assert is_eod[100_000.0] is True   # 21:00 UTC → EOD of the day
    assert is_eod[200_000.0] is False  # 23:00 UTC → after cash close, not EOD
