"""db-writer idempotency on a live Postgres (#339, audit 07#5).

The SQLite unit suite proves the ``ON CONFLICT DO NOTHING`` statement shape;
this one proves it against the real dialect and the real unique indexes
(migration 059): the same batch written twice, and a redelivered row with a
different payload, land exactly one row per natural key.

Runs in CI's ``live-integration`` job (``DB_RUN_INTEGRATION=1`` +
``DATABASE_URL`` on a migrated Postgres).
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.db_integration, pytest.mark.asyncio]

TAG = "TEST339"
TS = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _batch(label: str = "calm") -> list[tuple[str, dict[str, Any]]]:
    return [
        ("vol_surface_history", {"timestamp": TS, "underlying": TAG, "spot": 1.1, "forward": 1.1,
                                 "surface_data": {"k": 1}}),
        ("regime_snapshot_history", {"timestamp": TS, "symbol": TAG, "label": label, "method": "gmm"}),
        ("feature_history", {"timestamp": TS, "symbol": TAG, "iv_atm_1m_pct": 7.5}),
    ]


async def test_resend_and_redelivery_land_one_row_per_natural_key() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url or not os.environ.get("DB_RUN_INTEGRATION"):
        pytest.skip("db_integration: set DB_RUN_INTEGRATION=1 and DATABASE_URL")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import FeatureHistory, RegimeSnapshot, VolSurface
    from persistence.writer import AsyncDatabaseWriter

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    w = AsyncDatabaseWriter(session_factory=maker, retry_attempts=1)

    async def _count(model: Any, col: Any) -> int:
        async with maker() as db:
            return int((await db.execute(select(func.count()).select_from(model).where(col == TAG))).scalar_one())

    async def _cleanup() -> None:
        async with maker() as db:
            await db.execute(delete(VolSurface).where(VolSurface.underlying == TAG))
            await db.execute(delete(RegimeSnapshot).where(RegimeSnapshot.symbol == TAG))
            await db.execute(delete(FeatureHistory).where(FeatureHistory.symbol == TAG))
            await db.commit()

    await _cleanup()
    try:
        stmt = w._build_insert("regime_snapshot_history", [_batch()[1][1]], "postgresql")
        assert "ON CONFLICT" in str(stmt.compile(dialect=engine.dialect))

        await w._write_batch(_batch())
        await w._write_batch(_batch())                 # retry after an in-doubt commit
        await w._write_batch(_batch(label="stressed"))  # Redis redelivery, later payload

        assert await _count(VolSurface, VolSurface.underlying) == 1
        assert await _count(RegimeSnapshot, RegimeSnapshot.symbol) == 1
        assert await _count(FeatureHistory, FeatureHistory.symbol) == 1
        async with maker() as db:
            row = (await db.execute(select(RegimeSnapshot).where(RegimeSnapshot.symbol == TAG))).scalar_one()
        assert row.label == "calm", "first write wins; the redelivery is dropped, not applied"
    finally:
        await _cleanup()
        await engine.dispose()
