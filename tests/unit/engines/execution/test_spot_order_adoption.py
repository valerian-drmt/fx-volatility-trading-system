"""`adopt_open_spot_orders` — the execution-engine startup sweep that re-wires
IB's still-open CASH orders onto `spot_order` rows (#291).

Why : handlers attached at placement die with the engine process ; and the
first prod spot order (IB id 5, 2026-11-05) predates the table entirely. Both
must end up in the Orders blotter with a working Cancel button after a deploy.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.fixtures.db import make_sqlite_sessionmaker as _make_session  # shared harness (#338), FKs ON

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


class _Event:
    """Minimal stand-in for ib_async's Event (supports ``+=``)."""

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self


def _open_trade(*, sec_type="CASH", order_id=5, perm=422816514, ref="", action="SELL",
                qty=44_513.0, lmt=1.12325, status="PreSubmitted") -> SimpleNamespace:
    return SimpleNamespace(
        contract=SimpleNamespace(secType=sec_type, symbol="EUR", currency="USD",
                                 exchange="IDEALPRO", localSymbol="EUR.USD"),
        order=SimpleNamespace(orderId=order_id, permId=perm, orderRef=ref, action=action,
                              totalQuantity=qty, lmtPrice=lmt),
        orderStatus=SimpleNamespace(status=status),
        statusEvent=_Event(), fillEvent=_Event(), fills=[],
    )


async def _rows(maker):
    from sqlalchemy import select

    from persistence.models import SpotOrder
    async with maker() as db:
        return (await db.execute(select(SpotOrder).order_by(SpotOrder.id))).scalars().all()


async def test_untracked_cash_order_gets_a_row_and_handlers():
    from engines.execution.spot_orders import adopt_open_spot_orders
    maker, engine = await _make_session()
    try:
        t = _open_trade()
        fut = _open_trade(sec_type="FUT", order_id=9, perm=1)  # ignored: not CASH
        wired = await adopt_open_spot_orders([t, fut], maker)
        rows = await _rows(maker)
        assert len(rows) == 1 and wired == [rows[0].id]
        r = rows[0]
        assert (r.side, r.qty, r.limit_price) == ("SELL", 44_513, pytest.approx(1.12325))
        assert r.ib_order_id == "5" and r.ib_perm_id == "422816514"
        assert r.ib_local_symbol == "EUR.USD"
        assert r.state == "submitted"  # PreSubmitted synced on adoption
        assert len(t.statusEvent.handlers) == 1 and len(t.fillEvent.handlers) == 1
        assert fut.statusEvent.handlers == []
    finally:
        await engine.dispose()


async def test_known_order_ref_rewires_without_duplicating():
    from engines.execution.spot_orders import adopt_open_spot_orders, create_spot_order
    maker, engine = await _make_session()
    try:
        sid = await create_spot_order(maker, symbol="EUR", currency="USD", exchange="IDEALPRO",
                                      side="BUY", qty=100_000, limit_price=1.1237)
        t = _open_trade(order_id=11, perm=777, ref=f"fxvol:spot:{sid}", action="BUY",
                        qty=100_000.0, lmt=1.1237, status="Submitted")
        wired = await adopt_open_spot_orders([t], maker)
        rows = await _rows(maker)
        assert wired == [sid] and len(rows) == 1
        assert rows[0].ib_order_id == "11" and rows[0].state == "submitted"
        # A second sweep (e.g. another restart) matches on permId, still one row.
        t2 = _open_trade(order_id=11, perm=777, ref="", action="BUY", qty=100_000.0, lmt=1.1237)
        await adopt_open_spot_orders([t2], maker)
        assert len(await _rows(maker)) == 1
    finally:
        await engine.dispose()


async def test_a_bad_trade_object_does_not_break_the_sweep():
    from engines.execution.spot_orders import adopt_open_spot_orders
    maker, engine = await _make_session()
    try:
        bad = SimpleNamespace(contract=SimpleNamespace(secType="CASH"))  # no order/events
        good = _open_trade(order_id=6, perm=2)
        wired = await adopt_open_spot_orders([bad, good], maker)
        assert len(wired) == 1 and len(await _rows(maker)) == 1
    finally:
        await engine.dispose()


def test_parse_spot_order_ref():
    from engines.execution.spot_orders import parse_spot_order_ref
    assert parse_spot_order_ref("fxvol:spot:12") == 12
    assert parse_spot_order_ref("fxvol:12:3") is None
    assert parse_spot_order_ref("fxvol:spot:x") is None
    assert parse_spot_order_ref(None) is None
