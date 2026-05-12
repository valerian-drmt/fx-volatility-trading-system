"""Unit tests for api.orchestration.book_state_refresh.

Verifies that calling `refresh_book_state` :
  * sums entry-time greeks across all open positions,
  * flips the previous current row to historical,
  * inserts a new row with `is_current=True`,
  * carries `capital_total_usd` from the previous row.
"""
from __future__ import annotations

from datetime import UTC, datetime

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


async def test_refresh_sums_open_position_greeks():
    from sqlalchemy import select

    from api.orchestration.book_state_refresh import refresh_book_state
    from persistence.models import (
        BookStateSnapshot,
        BookedPosition,
        TradeStructure,
    )

    maker, engine = await _make_session()
    try:
        now = datetime.now(UTC)
        async with maker() as db:
            # Seed previous current snapshot (capital should be carried over)
            db.add(BookStateSnapshot(
                id=1, timestamp=now, symbol="EURUSD",
                total_vega_usd=999.0, total_gamma_usd=0.0, total_theta_usd=0.0,
                total_delta=0.0, n_open_structures=0, n_open_legs=0,
                notional_engaged_usd=0.0, capital_total_usd=200_000.0,
                margin_used_usd=0.0, is_current=True,
            ))
            # Two open structures + positions, one closed (skipped).
            for i, vega in enumerate([300.0, 200.0]):
                struct = TradeStructure(
                    id=i + 1,
                    structure_type="straddle", reference_tenor="3M",
                    base_qty=1, state="fully_filled", execution_mode="mock",
                )
                db.add(struct)
                await db.flush()
                db.add(BookedPosition(
                    id=i + 1,
                    structure_id=struct.id, opened_at=now,
                    entry_premium_usd=1000.0 + i * 100,
                    entry_total_cost_usd=20.0,
                    state="open",
                    entry_vega_usd_per_volpt=vega,
                    entry_gamma_usd_per_pip2=10.0,
                    entry_theta_usd_per_day=-5.0,
                ))
            # Closed position : must NOT be counted.
            struct_c = TradeStructure(
                id=99,
                structure_type="straddle", reference_tenor="3M",
                base_qty=1, state="fully_filled", execution_mode="mock",
            )
            db.add(struct_c)
            await db.flush()
            db.add(BookedPosition(
                id=99, structure_id=struct_c.id, opened_at=now,
                entry_premium_usd=500.0, entry_total_cost_usd=10.0,
                state="closed",
                entry_vega_usd_per_volpt=99999.0,  # poison value
            ))
            await db.commit()

            new_row = await refresh_book_state(db)
            await db.commit()

        async with maker() as db:
            currents = (await db.execute(
                select(BookStateSnapshot).where(BookStateSnapshot.is_current.is_(True))
            )).scalars().all()
            historicals = (await db.execute(
                select(BookStateSnapshot).where(BookStateSnapshot.is_current.is_(False))
            )).scalars().all()
        assert len(currents) == 1
        cur = currents[0]
        assert cur.total_vega_usd == pytest.approx(500.0)   # 300+200
        assert cur.total_gamma_usd == pytest.approx(20.0)
        assert cur.total_theta_usd == pytest.approx(-10.0)
        assert cur.n_open_structures == 2
        assert cur.capital_total_usd == pytest.approx(200_000.0)
        assert cur.notional_engaged_usd == pytest.approx(2100.0)  # 1000+1100
        assert len(historicals) == 1
        assert new_row.id == cur.id
    finally:
        await engine.dispose()


async def test_refresh_with_no_open_positions_writes_zero_row():
    from sqlalchemy import select

    from api.orchestration.book_state_refresh import refresh_book_state
    from persistence.models import BookStateSnapshot

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await refresh_book_state(db, capital_default=50_000.0)
            await db.commit()
        async with maker() as db:
            cur = (await db.execute(
                select(BookStateSnapshot).where(BookStateSnapshot.is_current.is_(True))
            )).scalar_one()
        assert cur.total_vega_usd == 0.0
        assert cur.n_open_structures == 0
        assert cur.capital_total_usd == 50_000.0
    finally:
        await engine.dispose()
