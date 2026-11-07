"""persistence.halt — the durable kill switch (#315)."""
from __future__ import annotations

import pytest

pytest.importorskip("pytest_asyncio")

from persistence.halt import get_halt, is_halted, set_halt
from tests.fixtures.db import make_sqlite_sessionmaker

pytestmark = pytest.mark.asyncio


async def test_flag_defaults_to_not_halted_and_round_trips_with_an_audit_event():
    maker, engine = await make_sqlite_sessionmaker()
    try:
        async with maker() as db:
            assert await is_halted(db) is False
            st = await set_halt(db, halted=True, reason="IB session hijacked", by="trader")
            await db.commit()
        assert st.halted and st.reason == "IB session hijacked"
        async with maker() as db:
            assert await is_halted(db) is True
            h = await get_halt(db)
            assert h.halted and h.reason == "IB session hijacked" and h.set_by == "trader"
            await set_halt(db, halted=False, reason="all clear", by="trader")
            await db.commit()
        async with maker() as db:
            assert await is_halted(db) is False
            assert (await get_halt(db)).halted is False
            from sqlalchemy import select

            from persistence.models import TradeEvent
            kinds = (await db.execute(select(TradeEvent.event_type))).scalars().all()
            assert kinds == ["trading_halted", "trading_resumed"]
    finally:
        await engine.dispose()


async def test_engine_guard_refuses_when_halted_and_when_the_flag_is_unreadable():
    from fastapi import HTTPException

    from engines.execution.halt_guard import check_not_halted

    maker, engine = await make_sqlite_sessionmaker()
    try:
        await check_not_halted(maker)  # not halted: no raise
        async with maker() as db:
            await set_halt(db, halted=True, reason="x", by="t")
            await db.commit()
        with pytest.raises(HTTPException) as e:
            await check_not_halted(maker)
        assert e.value.status_code == 423
    finally:
        await engine.dispose()

    class _BrokenMaker:
        def __call__(self):
            raise RuntimeError("db down")

    with pytest.raises(HTTPException) as e2:
        await check_not_halted(_BrokenMaker())  # type: ignore[arg-type]
    assert e2.value.status_code == 503


async def test_cancel_all_open_cancels_each_open_trade_and_calls_global_cancel():
    from types import SimpleNamespace

    from engines.execution.halt_guard import cancel_all_open

    class _IB:
        def __init__(self):
            self.cancelled = []
            self.global_called = False

        def openTrades(self):
            return [SimpleNamespace(order=SimpleNamespace(orderId=i)) for i in (5, 7)]

        def cancelOrder(self, order):
            self.cancelled.append(order.orderId)

        def reqGlobalCancel(self):
            self.global_called = True

    ib = _IB()
    out = await cancel_all_open(ib)
    assert ib.cancelled == [5, 7] and ib.global_called
    assert out == {"cancelled": 2, "failed": 0, "global_cancel": 1}
