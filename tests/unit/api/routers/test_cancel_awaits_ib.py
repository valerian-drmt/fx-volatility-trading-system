"""A cancel is true when IB confirms it (#487), and stage 3's other rules on
what a dead or waiting order does to the book (#485).

The api used to write ``cancelled`` as soon as the engine answered, and for a
row with no IB id it wrote ``cancelled`` without contacting IB at all.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from api.routers import trades as trades_router
from engines.execution.fills_handler import _on_order_status, _persist_fill
from engines.execution.reaper import live_ib_order_keys
from persistence.models import LegPosition, StructureFill, StructureOrder, TradeEvent, TradeStructure
from persistence.reservation import recompute_reservation

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


class _Client:
    """Stands in for httpx.AsyncClient: answers every DELETE with one status."""

    status = 200
    calls: list[str] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def delete(self, url: str, headers: Any = None) -> Any:
        _Client.calls.append(url)
        return SimpleNamespace(status_code=_Client.status, text="")


@pytest.fixture(autouse=True)
def _engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.calls = []
    _Client.status = 200
    monkeypatch.setattr(trades_router.httpx, "AsyncClient", _Client)


async def _close_in_flight(db: Any, *, ib_order_id: str | None = "40") -> tuple[int, int, int]:
    """A long 100 with a closing order of 100 working on it."""
    entry_s = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=100,
                             state="fully_filled", execution_mode="live")
    close_s = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=100,
                             state="submitted", execution_mode="live")
    db.add_all([entry_s, close_s])
    await db.flush()
    entry = StructureOrder(structure_id=entry_s.id, leg_idx=0, order_role="entry", contract_type="call", side="BUY",
                           qty=100, order_type="LMT", state="filled", qty_filled=100, ib_local_symbol="EUUZ6 C1210")
    db.add(entry)
    await db.flush()
    db.add(LegPosition(order_id=entry.id, open_qty=Decimal(100)))
    db.add(StructureFill(order_id=entry.id, ib_execution_id=f"entry{entry.id}", timestamp=NOW, qty_filled=100,
                         fill_price=0.0011, commission_usd=0.0, side="BUY"))
    close = StructureOrder(structure_id=close_s.id, leg_idx=0, order_role="closing", contract_type="call", side="SELL",
                           qty=100, order_type="LMT", limit_price=0.0001, state="submitted", submitted_at=NOW,
                           ib_local_symbol="EUUZ6 C1210", closes_order_id=entry.id, ib_order_id=ib_order_id)
    db.add(close)
    await db.flush()
    await recompute_reservation(db, entry_order_id=entry.id)
    await db.commit()
    return int(close_s.id), int(close.id), int(entry.id)


async def _reserved(db: Any, entry_id: int) -> float:
    lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == entry_id))).scalar_one()
    return float(lp.reserved_qty)


async def test_a_cancel_ib_accepted_is_not_recorded_until_ib_confirms(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        sid, oid, entry_id = await _close_in_flight(db)
        out = await trades_router.cancel_trade(sid, db)
        assert (out["cancelled"], out["cancel_requested"]) == (0, 1)
        assert out["results"][0]["status"] == "cancel_requested"
        row = await db.get(StructureOrder, oid)
        assert row.state == "submitted", "still working until IB says otherwise"
        assert await _reserved(db, entry_id) == 100, "and still holding the leg"
    assert _Client.calls[0].endswith("/internal/orders/40")

    # IB confirms: the status handler records it and frees the leg
    trade = SimpleNamespace(orderStatus=SimpleNamespace(status="Cancelled", whyHeld=""), log=[],
                            order=SimpleNamespace(orderRef="", orderId=40, permId=1))
    await _on_order_status(trade, oid, sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        assert (await db.get(StructureOrder, oid)).state == "cancelled"
        assert await _reserved(db, entry_id) == 0, "a close cancelled by IB releases what it reserved"


async def test_an_order_ib_does_not_have_is_cancelled_at_once(sqlite_sessionmaker: Any) -> None:
    _Client.status = 404
    async with sqlite_sessionmaker() as db:
        sid, oid, entry_id = await _close_in_flight(db)
        out = await trades_router.cancel_trade(sid, db)
        assert (out["cancelled"], out["cancel_requested"]) == (1, 0)
        assert (await db.get(StructureOrder, oid)).state == "cancelled"
        assert await _reserved(db, entry_id) == 0


async def test_a_row_without_an_ib_id_is_cancelled_by_its_reference(sqlite_sessionmaker: Any) -> None:
    """After a placement that timed out the row has no IB id. It used to be
    written cancelled without asking IB, while the order could be live."""
    async with sqlite_sessionmaker() as db:
        sid, oid, _ = await _close_in_flight(db, ib_order_id=None)
        out = await trades_router.cancel_trade(sid, db)
        assert out["cancel_requested"] == 1
        assert (await db.get(StructureOrder, oid)).state == "submitted"
    assert _Client.calls[0].endswith(f"/internal/orders/by-ref/{sid}/{oid}")


async def test_a_fill_on_an_ended_order_is_booked_without_reopening_it(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        _, oid, entry_id = await _close_in_flight(db)
        row = await db.get(StructureOrder, oid)
        row.state = "expired"          # the reaper gave up on it; IB still had it
        await db.commit()
    fill = SimpleNamespace(
        execution=SimpleNamespace(execId="late1", shares=30, price=0.0001, side="SLD", time=NOW, exchange="CME"),
        contract=SimpleNamespace(localSymbol="EUUZ6 C1210", conId=747303265, secType="FOP", symbol="EUR",
                                 currency="USD", exchange="CME", multiplier="125000",
                                 lastTradeDateOrContractMonth="20261204", strike=1.21, right="C"),
        commissionReport=SimpleNamespace(commission=0.0, currency="USD"), time=NOW,
    )
    await _persist_fill(fill, oid, sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
        assert (row.state, row.qty_filled) == ("expired", 30), "booked, and the row does not flip back"
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == entry_id))).scalar_one()
        assert float(lp.open_qty) == 70, "the execution reduces the leg it closes"
        ev = (await db.execute(select(TradeEvent).where(TradeEvent.event_type == "fill_on_ended_order"))).scalars().all()
        assert len(ev) == 1


def test_an_order_ib_holds_is_working_for_the_reaper() -> None:
    """#485: Inactive is an order IB holds (outside hours, price cap). It was
    not in the list, so it was expired after five minutes."""
    trades = [
        {"status": "Inactive", "remaining": 10, "order_id": 1, "perm_id": 11},
        {"status": "PendingCancel", "remaining": 10, "order_id": 2, "perm_id": 22},
        {"status": "Submitted", "remaining": 10, "order_id": 3, "perm_id": 33},
        {"status": "Cancelled", "remaining": 10, "order_id": 4, "perm_id": 44},
        {"status": "Filled", "remaining": 0, "order_id": 5, "perm_id": 55},
    ]
    assert live_ib_order_keys(trades) == {"1", "11", "2", "22", "3", "33"}
