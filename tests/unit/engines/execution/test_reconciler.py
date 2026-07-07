"""Unit tests for engines.execution.reconciler — materialised breaks (I4).

Every book↔broker divergence becomes a ``reconciliation_break`` ROW with a
lifecycle (detected → updated → resolved ; re-breaks open new rows). Includes
scenario T3 : two structures sharing a conid — legs exact in the book, the
mirror nets them, break = 0.
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


async def _seed_book_leg(db, *, qty: float, side: str = "BUY",
                         local_symbol: str = "EUUV6 C1130") -> int:
    from persistence.models import LegPosition, StructureOrder, TradeStructure
    s = TradeStructure(structure_type="strangle", reference_tenor="3M",
                       base_qty=int(abs(qty)), state="fully_filled",
                       execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry",
                       contract_type="call", side=side, qty=int(abs(qty)),
                       order_type="LMT", state="filled",
                       ib_local_symbol=local_symbol)
    db.add(o)
    await db.flush()
    signed = qty if side == "BUY" else -qty
    db.add(LegPosition(order_id=o.id, open_qty=signed, reserved_qty=0))
    return o.id


def _mirror(local_symbol: str, qty: float, side: str = "BUY"):
    from persistence.models import OpenPosition
    return OpenPosition(structure=local_symbol, side=side, quantity=qty,
                        entry_timestamp=datetime.now(UTC))


async def _open_breaks(maker):
    from sqlalchemy import select

    from persistence.models import ReconciliationBreak
    async with maker() as db:
        return (await db.execute(
            select(ReconciliationBreak)
            .where(ReconciliationBreak.resolved_at.is_(None))
        )).scalars().all()


async def test_quantity_break_lifecycle_detect_update_resolve():
    from sqlalchemy import select

    from engines.execution.reconciler import reconcile_positions
    from persistence.models import OpenPosition, ReconciliationBreak

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_book_leg(db, qty=10)
            db.add(_mirror("EUUV6 C1130", 6))
            await db.commit()

        assert await reconcile_positions(sessionmaker_factory=maker) == 1
        (brk,) = await _open_breaks(maker)
        assert brk.break_type == "quantity"
        assert float(brk.diff) == 4
        first_seen = brk.last_seen_at

        # Gap persists → same row updated, no duplicate.
        async with maker() as db:
            mir = (await db.execute(select(OpenPosition))).scalars().one()
            mir.quantity = 8
            await db.commit()
        assert await reconcile_positions(sessionmaker_factory=maker) == 1
        (brk,) = await _open_breaks(maker)
        assert float(brk.diff) == 2
        assert brk.last_seen_at >= first_seen

        # Sides agree → resolved, zero open rows, history kept.
        async with maker() as db:
            mir = (await db.execute(select(OpenPosition))).scalars().one()
            mir.quantity = 10
            await db.commit()
        assert await reconcile_positions(sessionmaker_factory=maker) == 0
        assert await _open_breaks(maker) == []
        async with maker() as db:
            all_rows = (await db.execute(select(ReconciliationBreak))).scalars().all()
        assert len(all_rows) == 1
        assert all_rows[0].resolved_at is not None
    finally:
        await engine.dispose()


async def test_t3_shared_conid_nets_to_zero_break():
    """Two structures on the SAME contract (long 10, short 4) : the book keeps
    the legs exact (+10 / −4), the mirror reports the net (+6) — and the
    reconciliation agrees : no break."""
    from engines.execution.reconciler import reconcile_positions

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_book_leg(db, qty=10, side="BUY")
            await _seed_book_leg(db, qty=4, side="SELL")
            db.add(_mirror("EUUV6 C1130", 6, side="BUY"))
            await db.commit()
        assert await reconcile_positions(sessionmaker_factory=maker) == 0
        assert await _open_breaks(maker) == []
    finally:
        await engine.dispose()


async def test_orphan_mirror_row_is_an_unbooked_break():
    from engines.execution.reconciler import reconcile_positions

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            db.add(_mirror("EUUU6 C1200", 3, side="SELL"))
            await db.commit()
        assert await reconcile_positions(sessionmaker_factory=maker) == 1
        (brk,) = await _open_breaks(maker)
        assert brk.break_type == "unbooked_at_ib"
        assert float(brk.broker_qty) == -3
    finally:
        await engine.dispose()


async def test_direction_and_missing_breaks_classified():
    from engines.execution.reconciler import reconcile_positions

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_book_leg(db, qty=10, side="BUY", local_symbol="EUUV6 C1130")
            db.add(_mirror("EUUV6 C1130", 10, side="SELL"))      # signs disagree
            await _seed_book_leg(db, qty=5, side="BUY", local_symbol="EUUV6 P1090")
            # P1090 absent from the mirror entirely → missing_at_ib
            await db.commit()
        assert await reconcile_positions(sessionmaker_factory=maker) == 2
        by_type = {b.break_type: b for b in await _open_breaks(maker)}
        assert set(by_type) == {"direction", "missing_at_ib"}
        assert by_type["direction"].local_symbol == "EUUV6 C1130"
        assert by_type["missing_at_ib"].local_symbol == "EUUV6 P1090"
    finally:
        await engine.dispose()


async def test_rebreak_after_resolution_opens_a_new_row():
    from sqlalchemy import select

    from engines.execution.reconciler import reconcile_positions
    from persistence.models import OpenPosition, ReconciliationBreak

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_book_leg(db, qty=10)
            db.add(_mirror("EUUV6 C1130", 6))
            await db.commit()
        await reconcile_positions(sessionmaker_factory=maker)   # break opens
        async with maker() as db:
            mir = (await db.execute(select(OpenPosition))).scalars().one()
            mir.quantity = 10
            await db.commit()
        await reconcile_positions(sessionmaker_factory=maker)   # resolves
        async with maker() as db:
            mir = (await db.execute(select(OpenPosition))).scalars().one()
            mir.quantity = 4
            await db.commit()
        await reconcile_positions(sessionmaker_factory=maker)   # re-breaks

        async with maker() as db:
            rows = (await db.execute(
                select(ReconciliationBreak).order_by(ReconciliationBreak.id)
            )).scalars().all()
        assert len(rows) == 2                       # audit history preserved
        assert rows[0].resolved_at is not None
        assert rows[1].resolved_at is None
        assert float(rows[1].diff) == 6
    finally:
        await engine.dispose()


async def test_classify_break_pure():
    """Pure classifier ; async only to match the module's asyncio mark."""
    from core.execution.reconciliation import classify_break

    assert classify_break(5.0, 5.0) is None
    assert classify_break(1.00004, 1.0) is None      # rounding noise, not a break
    assert classify_break(3.0, 0.0) == "missing_at_ib"
    assert classify_break(0.0, 2.0) == "unbooked_at_ib"
    assert classify_break(4.0, -4.0) == "direction"
    assert classify_break(10.0, 6.0) == "quantity"
