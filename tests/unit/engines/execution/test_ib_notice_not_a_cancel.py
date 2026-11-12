"""An IB notice on the error channel is not a cancel.

Prod, 2026-11-12: closes were sent without a time in force. IB applied its
preset and said so with notice 10349 ("Order TIF was set to DAY based on
order preset"). ib_async does not know that code as a warning and marked the
trade Cancelled; our handler wrote ``rejected``. The orders were live at IB:
two SELL 100 on EUUZ6 C1210 (orders 110 and 116) stayed working, shown as
"rejected", on a position of 100.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from engines.execution.fills_handler import _on_order_status
from engines.execution.ib_session import _attach_option_handlers
from engines.execution.ib_trade_log import ib_reason_from_trade, is_notice_cancel
from engines.execution.order_executor import DEFAULT_TIF
from persistence.models import StructureOrder, TradeEvent, TradeStructure

pytestmark = pytest.mark.unit

NOTICE = "Error 10349, reqId 31: Order TIF was set to DAY based on order preset"
REAL = "Error 201, reqId 7: Order rejected - reason: margin"


def _trade(status: str, *entries: tuple[int, str]) -> Any:
    return SimpleNamespace(
        orderStatus=SimpleNamespace(status=status, whyHeld=""),
        log=[SimpleNamespace(errorCode=c, message=m) for c, m in entries],
        order=SimpleNamespace(orderRef="", orderId=31, permId=555382556),
    )


def test_a_notice_is_not_a_reason_and_not_a_cancel() -> None:
    notice = _trade("Cancelled", (10349, NOTICE))
    assert is_notice_cancel(notice) is True
    assert ib_reason_from_trade(notice) is None


def test_a_real_rejection_is_still_a_rejection() -> None:
    real = _trade("Cancelled", (10349, NOTICE), (201, REAL))
    assert is_notice_cancel(real) is False
    assert ib_reason_from_trade(real) == REAL
    assert is_notice_cancel(_trade("Cancelled")) is False, "a cancel with no message is a cancel"
    assert is_notice_cancel(_trade("Submitted", (10349, NOTICE))) is False


def test_every_order_states_its_time_in_force() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[4] / "src/engines/execution/order_executor.py").read_text(encoding="utf-8")
    assert DEFAULT_TIF == "DAY"
    assert src.count("order.tif = DEFAULT_TIF") == 2, "the close path and the manual / spot path"


async def _order(db: Any, state: str = "submitted") -> StructureOrder:
    s = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=100,
                       state="submitted" if state == "submitted" else "fully_failed", execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="closing", contract_type="call", side="SELL",
                       qty=100, order_type="LMT", limit_price=0.0001, state=state, ib_local_symbol="EUUZ6 C1210",
                       rejection_text=NOTICE if state == "rejected" else None)
    db.add(o)
    await db.commit()
    return o


async def test_the_status_handler_leaves_a_live_order_alone(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        o = await _order(db)
        oid = int(o.id)
    await _on_order_status(_trade("Cancelled", (10349, NOTICE)), oid, sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        assert (await db.get(StructureOrder, oid)).state == "submitted"
    # a real rejection still lands
    await _on_order_status(_trade("Cancelled", (201, REAL)), oid, sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
        assert row.state == "rejected" and row.rejection_text == REAL


async def test_an_order_open_at_ib_is_revived_from_a_wrong_terminal_state(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        o = await _order(db, state="rejected")
        oid, sid = int(o.id), int(o.structure_id)
        class _Event:
            def __iadd__(self, _handler: Any) -> Any:
                return self

        open_trade = SimpleNamespace(
            contract=SimpleNamespace(secType="FOP"),
            order=SimpleNamespace(orderRef=f"fxvol:{sid}:{oid}", orderId=31, permId=555382556),
            orderStatus=SimpleNamespace(status="Submitted", whyHeld=""), log=[],
            statusEvent=_Event(), fillEvent=_Event(),
        )
        wired = await _attach_option_handlers(db, [open_trade], sqlite_sessionmaker, datetime.now(UTC))
        await db.commit()
        assert wired == [oid]
        row = await db.get(StructureOrder, oid)
        assert row.state == "submitted" and row.rejection_text is None and row.ib_order_id == "31"
        assert (await db.get(TradeStructure, sid)).state == "submitted"
        audit = (await db.execute(
            select(TradeEvent).where(TradeEvent.event_type == "order_revived_open_at_ib")
        )).scalars().all()
        assert len(audit) == 1 and audit[0].severity == "warning"
