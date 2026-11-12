"""The book nets (#462, #463): a close reduces the leg it closes, an event
moves a leg without a fill, and a trade whose legs are flat is closed.

Prod, trade 60: order 75 BUY 100 P1075, later closed by order 106 (trade 85,
``closes_order_id = 75``). Order 75 kept ``open_qty = +100`` for ever and the
Trade panel showed a leg nobody held.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from core.execution.projection import BookEntry, net_leg
from persistence.book_close import REASON_EXPIRY, close_after_leg_change, close_structure_if_flat
from persistence.models import (
    BookedPosition,
    LegPosition,
    PositionEvent,
    StructureFill,
    StructureOrder,
    TradeStructure,
)
from persistence.projection import rebuild_all, rebuild_leg

pytestmark = pytest.mark.unit

T0 = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
PUT = "EUUZ6 P1075"
CALL = "EUUZ6 C1210"
M = 125_000.0


# ── pure fold ────────────────────────────────────────────────────────────────

def test_a_close_reduces_the_leg_and_realises_pnl() -> None:
    net = net_leg(
        [BookEntry("BUY", 100, 0.0100), BookEntry("SELL", 40, 0.0150, own=False)], multiplier=M,
    )
    assert net.open_qty == 60
    assert net.avg_price == pytest.approx(0.0100)
    assert net.realized_pnl_usd == pytest.approx(0.005 * 40 * M)


def test_a_full_close_is_flat_and_keeps_the_entry_average() -> None:
    net = net_leg(
        [BookEntry("BUY", 100, 0.0100), BookEntry("SELL", 100, 0.0080, own=False)], multiplier=M,
    )
    assert net.open_qty == 0
    assert net.avg_price == pytest.approx(0.0100)
    assert net.realized_pnl_usd == pytest.approx(-0.002 * 100 * M)


def test_a_short_leg_closed_by_a_buy() -> None:
    net = net_leg(
        [BookEntry("SELL", 10, 0.0200), BookEntry("BUY", 10, 0.0050, own=False)], multiplier=M,
    )
    assert net.open_qty == 0 and net.realized_pnl_usd == pytest.approx(0.015 * 10 * M)


def test_no_entries_is_an_empty_leg() -> None:
    net = net_leg([], multiplier=M)
    assert net.open_qty == 0 and net.avg_price is None and net.realized_pnl_usd == 0


# ── projection on a database ─────────────────────────────────────────────────

async def _structure(db: Any, *, state: str = "fully_filled", stype: str = "long strangle 10d") -> TradeStructure:
    s = TradeStructure(structure_type=stype, reference_tenor="3M", base_qty=100, state=state, execution_mode="live")
    db.add(s)
    await db.flush()
    return s


async def _order(
    db: Any, s: TradeStructure, *, idx: int, side: str, qty: int, symbol: str, ctype: str = "put",
    role: str = "entry", closes: int | None = None, state: str = "filled", filled: int | None = None,
) -> StructureOrder:
    o = StructureOrder(
        structure_id=s.id, leg_idx=idx, order_role=role, contract_type=ctype, side=side, qty=qty,
        order_type="LMT", limit_price=0.01, preview_price=0.01, state=state, ib_local_symbol=symbol,
        closes_order_id=closes, qty_filled=qty if filled is None else filled, total_commission_usd=0.0,
    )
    db.add(o)
    await db.flush()
    return o


_seq = 0


async def _fill(db: Any, o: StructureOrder, qty: int, price: float, at: datetime, side: str | None = None) -> None:
    global _seq
    _seq += 1
    db.add(StructureFill(
        order_id=o.id, ib_execution_id=f"x{_seq}", timestamp=at, qty_filled=qty, fill_price=price,
        commission_usd=0.0, side=side or o.side,
    ))
    await db.flush()


async def _open_qty(db: Any, order_id: int) -> float:
    lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == order_id))).scalar_one()
    return float(lp.open_qty)


async def _trade_60(db: Any) -> tuple[TradeStructure, StructureOrder, StructureOrder]:
    s = await _structure(db)
    put = await _order(db, s, idx=0, side="BUY", qty=100, symbol=PUT)
    call = await _order(db, s, idx=1, side="BUY", qty=100, symbol=CALL, ctype="call")
    await _fill(db, put, 100, 0.0100, T0)
    await _fill(db, call, 100, 0.0050, T0)
    await rebuild_leg(db, order_id=put.id)
    await rebuild_leg(db, order_id=call.id)
    db.add(BookedPosition(structure_id=s.id, opened_at=T0, entry_premium_usd=187_500.0,
                          entry_total_cost_usd=100.0, state="open"))
    await db.flush()
    return s, put, call


async def test_closing_order_reduces_the_entry_leg_and_holds_nothing(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s, put, call = await _trade_60(db)
        t85 = await _structure(db, stype="vanilla_put")
        close = await _order(db, t85, idx=0, side="SELL", qty=100, symbol=PUT, role="closing", closes=put.id)
        await _fill(db, close, 100, 0.0130, T0 + timedelta(days=30))
        await rebuild_leg(db, order_id=close.id)   # the fill handler's call
        await db.flush()

        assert await _open_qty(db, put.id) == 0, "the close reduces the leg it closes"
        assert await _open_qty(db, close.id) == 0, "the close holds nothing"
        assert await _open_qty(db, call.id) == 100
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == put.id))).scalar_one()
        assert float(lp.realized_pnl_usd) == pytest.approx(0.003 * 100 * M)

        # one leg flat, one held: the trade stays open
        assert await close_after_leg_change(db, order_id=close.id) == []
        assert (await db.get(TradeStructure, s.id)).state == "fully_filled"


async def test_partial_close_leaves_the_remainder(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        _, put, _ = await _trade_60(db)
        t = await _structure(db, stype="vanilla_put")
        close = await _order(db, t, idx=0, side="SELL", qty=30, symbol=PUT, role="closing", closes=put.id)
        await _fill(db, close, 30, 0.0130, T0 + timedelta(days=1))
        await rebuild_leg(db, order_id=close.id)
        await db.flush()
        assert await _open_qty(db, put.id) == 70


async def test_trade_closes_when_every_leg_is_flat_and_the_booked_row_follows(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s, put, call = await _trade_60(db)
        t = await _structure(db, stype="vanilla_put")
        c1 = await _order(db, t, idx=0, side="SELL", qty=100, symbol=PUT, role="closing", closes=put.id)
        c2 = await _order(db, t, idx=1, side="SELL", qty=100, symbol=CALL, ctype="call", role="closing", closes=call.id)
        await _fill(db, c1, 100, 0.0130, T0 + timedelta(days=30))
        await rebuild_leg(db, order_id=c1.id)
        await _fill(db, c2, 100, 0.0040, T0 + timedelta(days=30))
        await rebuild_leg(db, order_id=c2.id)
        await db.flush()

        assert await close_after_leg_change(db, order_id=c2.id) == [s.id]
        await db.flush()
        struct = await db.get(TradeStructure, s.id)
        assert struct.state == "closed" and struct.close_reason == "closed_by_trade"
        pos = (await db.execute(select(BookedPosition).where(BookedPosition.structure_id == s.id))).scalar_one()
        assert pos.state == "closed"
        assert float(pos.gross_pnl_usd) == pytest.approx((0.003 - 0.001) * 100 * M)
        assert float(pos.net_pnl_usd) == pytest.approx((0.003 - 0.001) * 100 * M - 100.0)
        # the ticket of closing orders is not a trade to close
        assert (await db.get(TradeStructure, t.id)).state == "fully_filled"
        # idempotent
        assert await close_structure_if_flat(db, structure_id=s.id) is False


async def test_a_working_close_keeps_the_trade_open(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = await _structure(db, stype="vanilla_put")
        put = await _order(db, s, idx=0, side="BUY", qty=10, symbol=PUT)
        await _fill(db, put, 10, 0.01, T0)
        t = await _structure(db, stype="vanilla_put")
        c1 = await _order(db, t, idx=0, side="SELL", qty=10, symbol=PUT, role="closing", closes=put.id)
        await _fill(db, c1, 10, 0.01, T0 + timedelta(hours=1))
        await rebuild_leg(db, order_id=c1.id)
        # a second close on the same leg is still working at the broker
        await _order(db, t, idx=1, side="SELL", qty=1, symbol=PUT, role="closing", closes=put.id,
                     state="submitted", filled=0)
        await db.flush()
        assert await close_structure_if_flat(db, structure_id=s.id) is False


async def test_unattributed_close_keeps_its_own_quantity_so_the_contract_sum_holds(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        _, put, _ = await _trade_60(db)
        t = await _structure(db, stype="vanilla_put")
        close = await _order(db, t, idx=0, side="SELL", qty=100, symbol=PUT, role="closing", closes=None)
        await _fill(db, close, 100, 0.0130, T0 + timedelta(days=1))
        await rebuild_leg(db, order_id=close.id)
        await db.flush()
        assert await _open_qty(db, put.id) == 100
        assert await _open_qty(db, close.id) == -100   # nets per contract, as before


async def test_expiry_event_brings_the_leg_to_zero_and_the_trade_expires(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = await _structure(db, stype="vanilla_put")
        put = await _order(db, s, idx=0, side="BUY", qty=10, symbol=PUT)
        await _fill(db, put, 10, 0.0100, T0)
        db.add(BookedPosition(structure_id=s.id, opened_at=T0, entry_premium_usd=12_500.0,
                              entry_total_cost_usd=0.0, state="open"))
        db.add(PositionEvent(order_id=put.id, kind="expiry", side="SELL", qty=Decimal(10), price=Decimal(0),
                             occurred_at=T0 + timedelta(days=90), dedup_key="2026-12-04", source="test"))
        await db.flush()
        await rebuild_leg(db, order_id=put.id)
        await db.flush()
        assert await _open_qty(db, put.id) == 0
        assert await close_structure_if_flat(db, structure_id=s.id, reason=REASON_EXPIRY) is True
        pos = (await db.execute(select(BookedPosition).where(BookedPosition.structure_id == s.id))).scalar_one()
        assert pos.state == "expired"
        assert float(pos.gross_pnl_usd) == pytest.approx(-0.01 * 10 * M)   # the premium is lost


async def test_repair_event_gives_a_leg_without_fills_its_quantity(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = await _structure(db, stype="vanilla_put")
        put = await _order(db, s, idx=0, side="BUY", qty=10, symbol="EUUG7 P1120")
        db.add(PositionEvent(order_id=put.id, kind="repair", side="BUY", qty=Decimal(10), price=Decimal("0.0200"),
                             occurred_at=T0, dedup_key="repair", source="test"))
        await db.flush()
        await rebuild_leg(db, order_id=put.id)
        await db.flush()
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == put.id))).scalar_one()
        assert float(lp.open_qty) == 10 and float(lp.avg_price) == pytest.approx(0.02)


async def test_full_rebuild_equals_incremental(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        _, put, call = await _trade_60(db)
        t = await _structure(db, stype="vanilla_put")
        close = await _order(db, t, idx=0, side="SELL", qty=60, symbol=PUT, role="closing", closes=put.id)
        await _fill(db, close, 60, 0.0130, T0 + timedelta(days=2))
        await rebuild_leg(db, order_id=close.id)
        await db.flush()
        before = {oid: await _open_qty(db, oid) for oid in (put.id, call.id, close.id)}
        for lp in (await db.execute(select(LegPosition))).scalars().all():
            lp.open_qty = Decimal(999)   # corrupt, then recover from the log
        await db.flush()
        await rebuild_all(db)
        await db.flush()
        assert {oid: await _open_qty(db, oid) for oid in before} == before == {put.id: 40, call.id: 100, close.id: 0}
