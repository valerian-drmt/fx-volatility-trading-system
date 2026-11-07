"""engines.execution.placement — one claim + orderRef for every IB placement (#318).

The entry path had an atomic claim; hedges, unwinds, closes and manual orders
did not, so a retried request could place the same order twice. These tests
pin the helper on an in-memory DB with a fake IB: second claim refused, key
stamped on the order, ids stamped on the row, claim released when placeOrder
raises before anything reached IB.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from tests.fixtures.db import make_sqlite_sessionmaker as _make_session  # shared harness (#338), FKs ON

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


class _FakeIB:
    def __init__(self, *, fail: bool = False) -> None:
        self.placed: list[SimpleNamespace] = []
        self.fail = fail

    def placeOrder(self, contract, order):
        if self.fail:
            raise RuntimeError("socket not connected")
        self.placed.append(order)
        return SimpleNamespace(order=SimpleNamespace(orderId=41 + len(self.placed), permId=900 + len(self.placed)),
                               orderStatus=SimpleNamespace(status="Submitted"))


async def _seed_unwind(maker) -> tuple[int, int]:
    from persistence.models import StructureOrder, TradeStructure
    async with maker() as db:
        s = TradeStructure(structure_type="straddle", reference_tenor="3M", base_qty=1,
                           state="submitted", execution_mode="live")
        db.add(s)
        await db.flush()
        o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="unwind", contract_type="call",
                           contract_strike=1.085, contract_expiry=date(2026, 12, 18), side="SELL", qty=3,
                           order_type="LMT", limit_price=0.01, state="pending")
        db.add(o)
        await db.commit()
        return int(s.id), int(o.id)


async def test_claim_then_place_stamps_ref_and_ids_and_refuses_a_replay():
    from engines.execution.placement import AlreadyClaimed, place_with_claim
    from persistence.models import StructureOrder

    maker, engine = await _make_session()
    try:
        sid, oid = await _seed_unwind(maker)
        ib = _FakeIB()
        order = SimpleNamespace(action="SELL", totalQuantity=3, lmtPrice=0.01)
        trade = await place_with_claim(sm=maker, model=StructureOrder, row_id=oid,
                                       order_ref=f"fxvol:{sid}:{oid}", ib=ib, contract=object(), order=order)
        assert trade.order.orderId == 42
        assert ib.placed[0].orderRef == f"fxvol:{sid}:{oid}"
        async with maker() as db:
            row = await db.get(StructureOrder, oid)
        assert row.submit_claimed_at is not None
        assert row.ib_order_id == "42" and row.ib_perm_id == "901"
        assert row.state == "submitted" and row.submitted_at is not None
        # the replay: claim refused, nothing placed again
        with pytest.raises(AlreadyClaimed):
            await place_with_claim(sm=maker, model=StructureOrder, row_id=oid,
                                   order_ref=f"fxvol:{sid}:{oid}", ib=ib, contract=object(), order=order)
        assert len(ib.placed) == 1
    finally:
        await engine.dispose()


async def test_claim_released_when_place_order_raises_before_reaching_ib():
    from engines.execution.placement import place_with_claim
    from persistence.models import StructureOrder

    maker, engine = await _make_session()
    try:
        sid, oid = await _seed_unwind(maker)
        order = SimpleNamespace(action="SELL", totalQuantity=3, lmtPrice=0.01)
        with pytest.raises(RuntimeError):
            await place_with_claim(sm=maker, model=StructureOrder, row_id=oid, order_ref=f"fxvol:{sid}:{oid}",
                                   ib=_FakeIB(fail=True), contract=object(), order=order)
        async with maker() as db:
            row = await db.get(StructureOrder, oid)
        assert row.submit_claimed_at is None and row.ib_order_id is None and row.state == "pending"
        # a genuine retry now succeeds
        ok = _FakeIB()
        await place_with_claim(sm=maker, model=StructureOrder, row_id=oid, order_ref=f"fxvol:{sid}:{oid}",
                               ib=ok, contract=object(), order=order)
        assert len(ok.placed) == 1
    finally:
        await engine.dispose()


async def test_a_row_that_already_has_an_ib_id_cannot_be_claimed():
    """Rows placed before the claim column existed carry an ib_order_id and a
    NULL claim: they must read as taken, never be placed again."""
    from engines.execution.placement import claim_submit
    from persistence.models import StructureOrder

    maker, engine = await _make_session()
    try:
        _, oid = await _seed_unwind(maker)
        async with maker() as db:
            row = await db.get(StructureOrder, oid)
            row.ib_order_id = "7"
            await db.commit()
        assert await claim_submit(maker, StructureOrder, oid) is False
    finally:
        await engine.dispose()


async def test_hedge_row_claim_and_refs():
    from engines.execution.placement import claim_submit, hedge_order_ref, manual_order_ref
    from persistence.models import HedgeOrder

    maker, engine = await _make_session()
    try:
        from datetime import UTC, datetime

        from persistence.models import BookedPosition, TradeStructure
        async with maker() as db:
            # FKs are ON in the shared harness (#338): a hedge needs its booked position.
            ts = TradeStructure(structure_type="straddle", reference_tenor="3M", base_qty=1,
                                state="fully_filled", execution_mode="live")
            db.add(ts)
            await db.flush()
            db.add(BookedPosition(id=1, structure_id=ts.id, opened_at=datetime.now(UTC),
                                  entry_premium_usd=0.0, entry_total_cost_usd=0.0, state="open"))
            await db.flush()
            db.add(HedgeOrder(position_id=1, triggered_at=datetime.now(UTC), delta_imbalance_at_trigger=-1.4,
                              rebalance_threshold_used=0.05, hedge_qty=1, side="BUY", state="pending"))
            await db.commit()
        assert await claim_submit(maker, HedgeOrder, 1) is True
        assert await claim_submit(maker, HedgeOrder, 1) is False
        assert hedge_order_ref(1) == "fxvol:hedge:1"
        assert manual_order_ref().startswith("fxvol:manual:") and len(manual_order_ref()) > 15
    finally:
        await engine.dispose()
