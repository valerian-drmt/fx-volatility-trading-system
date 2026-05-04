"""Tests for api.orchestration.baseline_compute.compute_baseline."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


def _coerce_bigint_to_integer(metadata) -> None:
    from sqlalchemy import BigInteger, Integer
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()


async def _make_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base
    _coerce_bigint_to_integer(Base.metadata)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def test_compute_baseline_emits_insufficient_when_few_obs():
    """With <20 observations per (event_type, days_bucket, tod_bucket) cell,
    every row lands in status='insufficient'."""
    from sqlalchemy import select

    from api.orchestration.baseline_compute import compute_baseline
    from persistence.models import RegimeSnapshot, VolFeaturesContextBaseline

    maker, engine = await _make_session()
    try:
        # Seed 5 snapshots all in (event_type=none, days_bucket=4, tod=ny_close).
        async with maker() as db:
            base = datetime(2026, 5, 4, 17, 0, tzinfo=UTC)
            for i in range(5):
                db.add(RegimeSnapshot(
                    timestamp=base + timedelta(minutes=10 * i),
                    symbol="EURUSD", label="calm", method="x",
                    vol_level_z=Decimal(f"{0.1 * i:.4f}"),
                    vol_of_vol_z=Decimal("0.0"),
                    term_slope_z=Decimal("0.0"),
                    event_dampener=False,
                ))
            await db.commit()

        async with maker() as db:
            report = await compute_baseline(db)
        assert report["valid"] == 0
        assert report["insufficient"] == 3       # 1 cell × 3 features

        async with maker() as db:
            rows = (await db.execute(
                select(VolFeaturesContextBaseline)
            )).scalars().all()
        assert len(rows) == 3
        assert all(r.status == "insufficient" for r in rows)
        assert all(r.n_obs == 5 for r in rows)
    finally:
        await engine.dispose()


async def test_compute_baseline_marks_valid_at_threshold():
    """≥20 observations in the same cell → status='valid', mu and sigma populated."""
    from sqlalchemy import select

    from api.orchestration.baseline_compute import compute_baseline
    from persistence.models import RegimeSnapshot, VolFeaturesContextBaseline

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            base = datetime(2026, 5, 4, 17, 0, tzinfo=UTC)
            for i in range(25):
                db.add(RegimeSnapshot(
                    timestamp=base + timedelta(minutes=5 * i),
                    symbol="EURUSD", label="calm", method="x",
                    vol_level_z=Decimal(f"{0.1 * (i % 5):.4f}"),
                    vol_of_vol_z=Decimal("0.0"),
                    term_slope_z=Decimal("0.0"),
                    event_dampener=False,
                ))
            await db.commit()

        async with maker() as db:
            await compute_baseline(db)
        async with maker() as db:
            row = (await db.execute(
                select(VolFeaturesContextBaseline)
                .where(VolFeaturesContextBaseline.feature == "vol_level")
                .limit(1)
            )).scalar_one()
        assert row.status == "valid"
        assert row.n_obs == 25
        assert row.mu == pytest.approx(0.20, abs=0.01)
    finally:
        await engine.dispose()


async def test_compute_baseline_is_idempotent():
    """Running twice produces the same row count (UPSERT, not INSERT)."""
    from sqlalchemy import func, select

    from api.orchestration.baseline_compute import compute_baseline
    from persistence.models import RegimeSnapshot, VolFeaturesContextBaseline

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            db.add(RegimeSnapshot(
                timestamp=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
                symbol="EURUSD", label="calm", method="x",
                vol_level_z=Decimal("1.0"),
                vol_of_vol_z=Decimal("0.5"),
                term_slope_z=Decimal("-0.5"),
                event_dampener=False,
            ))
            await db.commit()
        async with maker() as db:
            await compute_baseline(db)
        async with maker() as db:
            await compute_baseline(db)
        async with maker() as db:
            n = (await db.execute(
                select(func.count()).select_from(VolFeaturesContextBaseline)
            )).scalar_one()
        assert n == 3                # 1 cell × 3 features ; not 6
    finally:
        await engine.dispose()
