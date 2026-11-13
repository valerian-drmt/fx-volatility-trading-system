"""The order reconciler settles a stuck order on evidence only (#484).

It used to mark any order still ``submitted`` or ``partially_filled`` as
filled at its full quantity when the broker held a matching contract for its
trade. A partly filled order still working at IB became "filled", and since
2026-11-12 the unfilled remainder was booked as a ``repair`` quantity.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from engines.execution.order_reconciler import reconcile_stuck_orders
from persistence.models import (
    LegPosition,
    OpenPosition,
    PositionEvent,
    StructureFill,
    StructureOrder,
    TradeStructure,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
CALL = "EUUG7 C1110"


class _Executor:
    def __init__(self, fills: list[Any] | None = None, *, reporting: bool = True) -> None:
        self._fills, self._reporting, self.asked = fills or [], reporting, 0

    def is_connected(self) -> bool:
        return True

    def account_is_reporting(self) -> bool:
        return self._reporting

    async def replay_executions(self) -> list[Any]:
        self.asked += 1
        return list(self._fills)


def _fill(sid: int, oid: int, exec_id: str, qty: int, price: float = 0.02) -> Any:
    return SimpleNamespace(
        execution=SimpleNamespace(execId=exec_id, shares=qty, price=price, side="BOT", time=NOW,
                                  orderRef=f"fxvol:{sid}:{oid}", exchange="CME"),
        contract=SimpleNamespace(localSymbol=CALL, conId=853091580, secType="FOP", symbol="EUR",
                                 currency="USD", exchange="CME", multiplier="125000",
                                 lastTradeDateOrContractMonth="20270205", strike=1.11, right="C"),
        commissionReport=SimpleNamespace(commission=0.0, currency="USD"),
        time=NOW,
    )


async def _order(db: Any, *, qty: int, state: str, recorded: int = 0) -> StructureOrder:
    s = TradeStructure(structure_type="vanilla_call", reference_tenor="3M", base_qty=qty,
                       state="submitted", execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry", contract_type="call", side="BUY",
                       qty=qty, order_type="LMT", limit_price=0.02, preview_price=0.02, state=state,
                       contract_strike=1.11, ib_local_symbol=CALL, qty_filled=recorded)
    db.add(o)
    await db.flush()
    if recorded:
        db.add(StructureFill(order_id=o.id, ib_execution_id=f"r{o.id}", timestamp=NOW, qty_filled=recorded,
                             fill_price=0.02, commission_usd=0.0, side="BUY"))
    await db.commit()
    return o


async def test_a_partly_filled_order_the_broker_holds_stays_partly_filled(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        o = await _order(db, qty=10, state="partially_filled", recorded=3)
        oid, sid = int(o.id), int(o.structure_id)
        # the broker holds 3 of the contract, attributed to this trade
        db.add(OpenPosition(structure=CALL, side="BUY", quantity=Decimal(3), trade_id=sid, entry_timestamp=NOW))
        await db.commit()

    out = await reconcile_stuck_orders(sqlite_sessionmaker, _Executor([]))
    assert out["filled"] == 0 and out["from_ib"] == 0 and out["asked_ib"] is True
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
        assert (row.state, row.qty_filled) == ("partially_filled", 3), "a position is not an execution"
        assert (await db.execute(select(PositionEvent))).scalars().all() == [], "no quantity is invented"


async def test_a_new_order_is_not_filled_by_the_position_of_an_older_trade(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        o = await _order(db, qty=10, state="submitted")
        oid, sid = int(o.id), int(o.structure_id)
        db.add(OpenPosition(structure=CALL, side="BUY", quantity=Decimal(10), trade_id=sid, entry_timestamp=NOW))
        await db.commit()
    await reconcile_stuck_orders(sqlite_sessionmaker, _Executor([]))
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
        assert (row.state, row.qty_filled) == ("submitted", 0)


async def test_executions_ib_reports_for_the_order_are_booked(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        o = await _order(db, qty=10, state="submitted")
        oid, sid = int(o.id), int(o.structure_id)
    ex = _Executor([_fill(sid, oid, "e1", 4), _fill(sid, oid, "e2", 6), _fill(999, 999, "other", 5)])
    out = await reconcile_stuck_orders(sqlite_sessionmaker, ex)
    assert out["from_ib"] == 2
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
        assert (row.state, row.qty_filled) == ("filled", 10)
        fills = (await db.execute(select(StructureFill).where(StructureFill.order_id == oid))).scalars().all()
        assert sorted(f.ib_execution_id for f in fills) == ["e1", "e2"]
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == oid))).scalar_one()
        assert float(lp.open_qty) == 10
    # idempotent: the same executions again book nothing new
    await reconcile_stuck_orders(sqlite_sessionmaker, ex)
    async with sqlite_sessionmaker() as db:
        assert len((await db.execute(select(StructureFill))).scalars().all()) == 2


async def test_recorded_fills_still_settle_a_row_that_never_advanced(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        o = await _order(db, qty=10, state="submitted", recorded=10)
        oid = int(o.id)
    out = await reconcile_stuck_orders(sqlite_sessionmaker, None)
    assert out["filled"] == 1 and out["asked_ib"] is False
    async with sqlite_sessionmaker() as db:
        assert (await db.get(StructureOrder, oid)).state == "filled"


async def test_ib_is_not_asked_and_nothing_is_guessed_on_a_dead_feed(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        o = await _order(db, qty=10, state="submitted")
        oid = int(o.id)
    ex = _Executor([], reporting=False)
    out = await reconcile_stuck_orders(sqlite_sessionmaker, ex)
    assert out["asked_ib"] is False and ex.asked == 0
    async with sqlite_sessionmaker() as db:
        assert (await db.get(StructureOrder, oid)).state == "submitted"
