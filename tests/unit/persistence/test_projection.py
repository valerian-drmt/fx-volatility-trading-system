"""Unit tests for persistence.projection — the forward leg-position fold.

Invariant I3 (OMS_ARCHITECTURE_CIBLE.md §7.1) : a leg's book position is a
pure fold over ITS OWN fills, linked by FK (entry order + closing orders via
``closes_order_id``) — never derived from the netted IB mirror. Includes
scenario T8 : destroy the projection, replay the fill log, get the identical
book back.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


async def _seed_leg(db, *, qty: int = 10, side: str = "BUY",
                    role: str = "entry", closes_order_id: int | None = None,
                    local_symbol: str | None = "EUUV6 C1130") -> int:
    from persistence.models import StructureOrder, TradeStructure
    s = TradeStructure(structure_type="strangle", reference_tenor="3M",
                       base_qty=qty, state="submitted", execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(
        structure_id=s.id, leg_idx=0, order_role=role,
        contract_type="call", side=side, qty=qty, order_type="LMT",
        state="filled", ib_local_symbol=local_symbol,
        closes_order_id=closes_order_id,
    )
    db.add(o)
    await db.flush()
    return o.id


_T0 = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _fill(order_id: int, *, exec_id: str, qty: int, price: float,
          side: str, minutes: int = 0):
    from persistence.models import StructureFill
    return StructureFill(
        order_id=order_id, ib_execution_id=exec_id,
        timestamp=_T0 + timedelta(minutes=minutes),
        qty_filled=qty, fill_price=price, commission_usd=1.0, side=side,
    )


async def test_project_leg_folds_entry_and_linked_closes():
    """Entry BUY 10 @1.20 (two partials), linked close SELL 6 @1.30 :
    open 4 @1.20, realised > 0 — all via FK, chronological."""
    from persistence.projection import project_leg

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, qty=10, side="BUY")
            close = await _seed_leg(db, qty=6, side="SELL", role="closing",
                                    closes_order_id=leg)
            db.add_all([
                _fill(leg, exec_id="e1", qty=6, price=1.20, side="BUY", minutes=0),
                _fill(leg, exec_id="e2", qty=4, price=1.20, side="BUY", minutes=1),
                _fill(close, exec_id="c1", qty=6, price=1.30, side="SELL", minutes=5),
            ])
            await db.commit()
            proj = await project_leg(db, order_id=leg)
        assert proj.open_qty == 4
        assert proj.avg_price == pytest.approx(1.20)
        assert proj.realized_pnl_usd > 0          # (1.30−1.20)·6·mult − commissions
        assert proj.n_fills == 3
    finally:
        await engine.dispose()


async def test_unlinked_close_does_not_touch_the_leg():
    """A closing order with closes_order_id NULL (orphan close) folds into
    nothing — the leg keeps its full open qty ; reconciliation, not guessing,
    surfaces the gap."""
    from persistence.projection import project_leg

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, qty=10, side="BUY")
            orphan_close = await _seed_leg(db, qty=6, side="SELL", role="closing",
                                           closes_order_id=None)
            db.add_all([
                _fill(leg, exec_id="e1", qty=10, price=1.20, side="BUY"),
                _fill(orphan_close, exec_id="c1", qty=6, price=1.30, side="SELL"),
            ])
            await db.commit()
            proj = await project_leg(db, order_id=leg)
        assert proj.open_qty == 10
    finally:
        await engine.dispose()


async def test_rebuild_preserves_reserved_qty():
    """reserved_qty belongs to the close-reservation ledger (spec §8) — a
    projection rebuild must never reset it."""
    from persistence.models import LegPosition
    from persistence.projection import rebuild_leg

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, qty=10)
            db.add(_fill(leg, exec_id="e1", qty=10, price=1.20, side="BUY"))
            db.add(LegPosition(order_id=leg, open_qty=0, reserved_qty=3))
            await db.commit()
            row = await rebuild_leg(db, order_id=leg)
            await db.commit()
        assert float(row.open_qty) == 10
        assert float(row.reserved_qty) == 3
    finally:
        await engine.dispose()


async def test_t8_projection_rebuilds_identically_after_destruction():
    """T8 : delete every leg_position row, replay the fill log via
    rebuild_all — the book comes back identical. Proves the projection has
    no hidden source of truth."""
    from sqlalchemy import delete, select

    from persistence.models import LegPosition
    from persistence.projection import rebuild_all

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg_a = await _seed_leg(db, qty=10, side="BUY")
            leg_b = await _seed_leg(db, qty=4, side="SELL",
                                    local_symbol="EUUV6 P1090")
            close_a = await _seed_leg(db, qty=6, side="SELL", role="closing",
                                      closes_order_id=leg_a)
            db.add_all([
                _fill(leg_a, exec_id="a1", qty=10, price=1.20, side="BUY"),
                _fill(leg_b, exec_id="b1", qty=4, price=0.90, side="SELL", minutes=1),
                _fill(close_a, exec_id="ca", qty=6, price=1.30, side="SELL", minutes=9),
            ])
            await db.commit()

            await rebuild_all(db)
            await db.commit()
            baseline = {
                r.order_id: (float(r.open_qty), r.avg_price, r.realized_pnl_usd)
                for r in (await db.execute(select(LegPosition))).scalars().all()
            }
            assert baseline[leg_a][0] == 4
            assert baseline[leg_b][0] == -4

            await db.execute(delete(LegPosition))    # destroy the projection
            await db.commit()
            n = await rebuild_all(db)
            await db.commit()
            rebuilt = {
                r.order_id: (float(r.open_qty), r.avg_price, r.realized_pnl_usd)
                for r in (await db.execute(select(LegPosition))).scalars().all()
            }
        assert n == len(rebuilt)
        assert rebuilt == baseline
    finally:
        await engine.dispose()
