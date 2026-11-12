"""A manual single-contract ticket is booked (#466); spot is cash, not a break."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from engines.execution.manual_tickets import book_manual_ticket, fail_manual_ticket
from engines.execution.reconciler import _broker_by_contract
from persistence.models import LegPosition, OpenPosition, StructureFill, StructureOrder, TradeStructure
from persistence.projection import rebuild_leg

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
FOP = {"symbol": "EUU", "sec_type": "FOP", "expiry": "20270205", "strike": 1.12, "right": "P", "limit_price": 0.012}


async def _order(db: Any, order_id: int) -> StructureOrder:
    return (await db.execute(select(StructureOrder).where(StructureOrder.id == order_id))).scalar_one()


async def test_a_ticket_on_a_contract_nobody_holds_is_a_new_one_leg_trade(sqlite_sessionmaker: Any) -> None:
    sid, oid = await book_manual_ticket(sqlite_sessionmaker, side="BUY", qty=10, **FOP)
    async with sqlite_sessionmaker() as db:
        o = await _order(db, oid)
        s = await db.get(TradeStructure, sid)
        assert (o.order_role, o.closes_order_id, o.state) == ("entry", None, "pending")
        assert (o.contract_type, o.contract_symbol, o.contract_expiry) == ("put", "EUR", date(2027, 2, 5))
        assert (s.structure_type, s.state, s.execution_mode) == ("vanilla_put", "submitted", "live")


async def test_a_ticket_that_reduces_a_held_leg_is_booked_as_its_close(sqlite_sessionmaker: Any) -> None:
    sid, oid = await book_manual_ticket(sqlite_sessionmaker, side="BUY", qty=10, **FOP)
    async with sqlite_sessionmaker() as db:
        o = await _order(db, oid)
        o.state, o.qty_filled, o.ib_local_symbol = "filled", 10, "EUUG7 P1120"
        db.add(StructureFill(order_id=oid, ib_execution_id="a", timestamp=NOW, qty_filled=10, fill_price=0.012,
                             commission_usd=0.0, side="BUY"))
        await db.flush()
        await rebuild_leg(db, order_id=oid)
        await db.commit()

    _, close_id = await book_manual_ticket(sqlite_sessionmaker, side="SELL", qty=4, **FOP)
    async with sqlite_sessionmaker() as db:
        c = await _order(db, close_id)
        assert (c.order_role, c.closes_order_id) == ("closing", oid)
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == oid))).scalar_one()
        assert float(lp.reserved_qty) == 4, "the close in flight reserves the leg"

    # more than what is left available: not a close of that leg, a trade of its own
    _, other = await book_manual_ticket(sqlite_sessionmaker, side="SELL", qty=7, **FOP)
    async with sqlite_sessionmaker() as db:
        assert (await _order(db, other)).order_role == "entry"

    await fail_manual_ticket(sqlite_sessionmaker, structure_id=sid, order_id=close_id, reason="ib down")
    async with sqlite_sessionmaker() as db:
        assert (await _order(db, close_id)).state == "rejected"
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == oid))).scalar_one()
        assert float(lp.reserved_qty) == 0, "a ticket that never reached IB releases the leg"


async def test_spot_tickets_are_not_booked_here(sqlite_sessionmaker: Any) -> None:
    with pytest.raises(ValueError, match="manual tickets are booked"):
        await book_manual_ticket(sqlite_sessionmaker, symbol="EUR", sec_type="CASH", side="SELL", qty=22_533,
                                 limit_price=1.1, expiry=None, strike=None, right=None)


async def test_spot_is_not_a_reconciliation_break(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        db.add(OpenPosition(structure="EUR.USD", side="SELL", quantity=Decimal(22_533), entry_timestamp=NOW))
        db.add(OpenPosition(structure="6EH7", side="BUY", quantity=Decimal(7), entry_timestamp=NOW))
        await db.flush()
        assert await _broker_by_contract(db) == {"6EH7": 7.0}
