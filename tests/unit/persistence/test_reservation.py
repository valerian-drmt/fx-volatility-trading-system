"""Unit tests for the close-reservation ledger (invariant I5, spec §8).

Scenario coverage from OMS_ARCHITECTURE_CIBLE.md §13 :
  * T4 — double-click close : the second reserve is refused atomically.
  * T5 — partial fill 7/17 then residual dies : the reservation re-folds to
    the outstanding remainder, then to zero on terminalisation.
"""
from __future__ import annotations

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


async def _seed_leg(db, *, open_qty: float = 10) -> int:
    from persistence.models import LegPosition, StructureOrder, TradeStructure
    s = TradeStructure(structure_type="strangle", reference_tenor="3M",
                       base_qty=int(abs(open_qty)), state="fully_filled",
                       execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry",
                       contract_type="call", side="BUY", qty=int(abs(open_qty)),
                       order_type="LMT", state="filled",
                       ib_local_symbol="EUUV6 C1130")
    db.add(o)
    await db.flush()
    db.add(LegPosition(order_id=o.id, open_qty=open_qty, reserved_qty=0))
    return o.id


async def _add_close(db, *, leg_id: int, qty: int, state: str = "submitted",
                     qty_filled: int = 0) -> int:
    from persistence.models import StructureOrder, TradeStructure
    s = TradeStructure(structure_type="vanilla_call", reference_tenor="3M",
                       base_qty=qty, state="submitted", execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="closing",
                       contract_type="call", side="SELL", qty=qty,
                       qty_filled=qty_filled, order_type="LMT", state=state,
                       closes_order_id=leg_id)
    db.add(o)
    await db.flush()
    return o.id


async def _reserved(maker, leg_id: int) -> float:
    from sqlalchemy import select

    from persistence.models import LegPosition
    async with maker() as db:
        row = (await db.execute(
            select(LegPosition).where(LegPosition.order_id == leg_id)
        )).scalars().one()
        return float(row.reserved_qty)


# ── T4 : double-click close — the second reserve is refused ─────────────────

async def test_t4_second_reserve_beyond_available_is_refused():
    from persistence.reservation import try_reserve_on_leg

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, open_qty=10)
            await db.commit()

        async with maker() as db:
            assert await try_reserve_on_leg(db, leg_order_id=leg, qty=10) is True
            await db.commit()
        assert await _reserved(maker, leg) == 10

        async with maker() as db:   # the double-click : nothing available
            assert await try_reserve_on_leg(db, leg_order_id=leg, qty=5) is False
            await db.commit()
        assert await _reserved(maker, leg) == 10          # unchanged, never > |open|
    finally:
        await engine.dispose()


async def test_partial_reserve_leaves_remainder_available():
    from persistence.reservation import try_reserve_on_leg

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, open_qty=10)
            assert await try_reserve_on_leg(db, leg_order_id=leg, qty=7) is True
            assert await try_reserve_on_leg(db, leg_order_id=leg, qty=3) is True
            assert await try_reserve_on_leg(db, leg_order_id=leg, qty=1) is False
            await db.commit()
        assert await _reserved(maker, leg) == 10
    finally:
        await engine.dispose()


async def test_short_leg_reserves_against_abs_open():
    """A short leg (open −4) has 4 closeable : the guard works on |open|."""
    from persistence.reservation import try_reserve_on_leg

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, open_qty=-4)
            assert await try_reserve_on_leg(db, leg_order_id=leg, qty=4) is True
            assert await try_reserve_on_leg(db, leg_order_id=leg, qty=1) is False
            await db.commit()
    finally:
        await engine.dispose()


# ── T5 : partial fill then dead residual — reservation re-folds ─────────────

async def test_t5_recompute_releases_filled_part_then_residual():
    from persistence.models import StructureOrder
    from persistence.reservation import recompute_reservation

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, open_qty=17)
            close = await _add_close(db, leg_id=leg, qty=17, state="submitted")
            await recompute_reservation(db, leg_order_id=leg)
            await db.commit()
        assert await _reserved(maker, leg) == 17           # close in flight

        async with maker() as db:   # 7/17 fills → outstanding 10
            o = await db.get(StructureOrder, close)
            o.qty_filled = 7
            o.state = "partially_filled"
            await recompute_reservation(db, leg_order_id=leg)
            await db.commit()
        assert await _reserved(maker, leg) == 10

        async with maker() as db:   # residual dies (reaper/cancel) → 0
            o = await db.get(StructureOrder, close)
            o.state = "expired"
            await recompute_reservation(db, leg_order_id=leg)
            await db.commit()
        assert await _reserved(maker, leg) == 0
    finally:
        await engine.dispose()


async def test_recompute_sums_multiple_inflight_closes():
    from persistence.reservation import recompute_reservation

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            leg = await _seed_leg(db, open_qty=10)
            await _add_close(db, leg_id=leg, qty=4, state="submitted")
            await _add_close(db, leg_id=leg, qty=3, state="pending")
            await _add_close(db, leg_id=leg, qty=2, state="cancelled")  # terminal
            await recompute_reservation(db, leg_order_id=leg)
            await db.commit()
        assert await _reserved(maker, leg) == 7
    finally:
        await engine.dispose()
