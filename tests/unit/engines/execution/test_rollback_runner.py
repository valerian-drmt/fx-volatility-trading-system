"""Unit tests for engines.execution.rollback_runner.

The pure decision logic lives in core.execution.rollback (already covered).
Here we verify the *runner* :
  * `load_order_states` materialises the OrderState list from the DB.
  * `run_rollback` issues `ib.cancelOrder` for cancellable legs, places
    opposite-side LimitOrders for partials, persists `unwind` rows, and
    writes audit log entries with the right event_type.

`ib_insync` is not imported by tests ; we monkey-patch the module's
`Contract` and `LimitOrder` symbols to lightweight stubs.
"""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from types import ModuleType, SimpleNamespace

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


# --- Stub ib_insync ---------------------------------------------------------
def _install_ibinsync_stub(monkeypatch):
    """Inject a fake ib_insync module so rollback_runner can import Contract /
    LimitOrder without bringing in the real lib (which we don't want to drive
    in unit tests).
    """
    fake_mod = ModuleType("ib_insync")

    class _Contract:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _LimitOrder:
        def __init__(self, action, totalQuantity, lmtPrice):
            self.action = action
            self.totalQuantity = totalQuantity
            self.lmtPrice = lmtPrice
            self.tif = "DAY"

    fake_mod.Contract = _Contract
    fake_mod.LimitOrder = _LimitOrder
    monkeypatch.setitem(sys.modules, "ib_insync", fake_mod)


class _FakeTrade:
    def __init__(self, *, order_id: int):
        self.order = SimpleNamespace(orderId=order_id, permId=order_id * 10)
        self.statusEvent = _Slot()
        self.fillEvent = _Slot()


class _Slot:
    def __init__(self):
        self._listeners = []

    def __iadd__(self, fn):
        self._listeners.append(fn)
        return self


class _FakeIB:
    def __init__(self):
        self.cancelled: list[int] = []
        self.placed: list[dict] = []
        self._next_order_id = 100

    def openTrades(self):
        return self._open

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)

    def placeOrder(self, contract, order):
        self.placed.append({
            "action": order.action, "qty": order.totalQuantity,
            "lmt": order.lmtPrice,
        })
        self._next_order_id += 1
        return _FakeTrade(order_id=self._next_order_id)

    async def qualifyContractsAsync(self, contract):
        return [contract]


class _FakeExecutor:
    def __init__(self, ib):
        self._ib = ib

    def is_connected(self) -> bool:
        return True

    def _ensure(self):
        return self._ib


# --- Tests ------------------------------------------------------------------

async def _seed_structure_with_orders(maker, *, configs):
    from persistence.models import StructureOrder, TradeStructure
    async with maker() as db:
        s = TradeStructure(
            structure_type="straddle", reference_tenor="3M",
            base_qty=5, state="submitted", execution_mode="live",
        )
        db.add(s)
        await db.flush()
        order_ids = []
        for i, cfg in enumerate(configs):
            o = StructureOrder(
                structure_id=s.id, leg_idx=i, order_role="entry",
                contract_type=cfg.get("contract_type", "call"),
                contract_strike=cfg.get("strike", 1.0850),
                contract_expiry=cfg.get("expiry", date(2026, 6, 19)),
                side=cfg["side"], qty=cfg["qty"],
                order_type="LMT", limit_price=1.0,
                preview_price=1.0,
                state=cfg["state"],
                qty_filled=cfg.get("qty_filled", 0),
                ib_order_id=cfg.get("ib_order_id"),
            )
            db.add(o)
            await db.flush()
            order_ids.append(o.id)
        await db.commit()
        return s.id, order_ids


async def test_run_rollback_cancels_pending_and_unwinds_partial(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.rollback_runner import run_rollback
    from persistence.models import StructureOrder, TradeEvent

    maker, engine = await _make_session()
    try:
        struct_id, order_ids = await _seed_structure_with_orders(
            maker,
            configs=[
                # Leg 0 : pending (cancellable, no fills) — pure cancel.
                {"side": "BUY", "qty": 5, "state": "submitted",
                 "ib_order_id": "1001"},
                # Leg 1 : partially_filled (cancel + unwind 3).
                {"side": "BUY", "qty": 5, "state": "partially_filled",
                 "qty_filled": 3, "ib_order_id": "1002"},
                # Leg 2 : already cancelled, qty_filled=0 → noop.
                {"side": "SELL", "qty": 5, "state": "cancelled",
                 "ib_order_id": "1003"},
            ],
        )

        ib = _FakeIB()
        # Pre-populate openTrades so cancel matches.
        ib._open = [
            SimpleNamespace(order=SimpleNamespace(orderId=1001)),
            SimpleNamespace(order=SimpleNamespace(orderId=1002)),
        ]
        executor = _FakeExecutor(ib)

        result = await run_rollback(
            sessionmaker_factory=maker, executor=executor,
            structure_id=struct_id,
        )

        assert result["noop"] is False
        assert sorted(ib.cancelled) == [1001, 1002]
        # 1 unwind placed (leg 1, opposite side SELL, qty=3 from partial fill)
        assert len(ib.placed) == 1
        assert ib.placed[0]["action"] == "SELL"
        assert ib.placed[0]["qty"] == 3

        async with maker() as db:
            from sqlalchemy import select
            unwind_orders = (await db.execute(
                select(StructureOrder)
                .where(StructureOrder.structure_id == struct_id)
                .where(StructureOrder.order_role == "unwind")
            )).scalars().all()
            audit = (await db.execute(
                select(TradeEvent)
                .where(TradeEvent.structure_id == struct_id)
            )).scalars().all()
        assert len(unwind_orders) == 1
        assert unwind_orders[0].side == "SELL"
        assert unwind_orders[0].qty == 3
        assert unwind_orders[0].state == "submitted"
        event_types = {a.event_type for a in audit}
        assert "order_cancelled" in event_types
        assert "unwind_order_created" in event_types
    finally:
        await engine.dispose()


async def test_run_rollback_noop_when_all_filled_or_cancelled(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.rollback_runner import run_rollback

    maker, engine = await _make_session()
    try:
        struct_id, _ = await _seed_structure_with_orders(
            maker,
            configs=[
                # All filled → no cancel, no unwind (per decide_rollback rules,
                # filled+qty_filled>0 also triggers unwind ; we set state=filled
                # but qty_filled=0 to genuinely test the noop branch).
                {"side": "BUY", "qty": 5, "state": "cancelled", "qty_filled": 0},
                {"side": "SELL", "qty": 5, "state": "rejected", "qty_filled": 0},
            ],
        )
        executor = _FakeExecutor(_FakeIB())
        result = await run_rollback(
            sessionmaker_factory=maker, executor=executor,
            structure_id=struct_id,
        )
        assert result["noop"] is True
        assert result["cancelled"] == []
        assert result["unwound"] == []
    finally:
        await engine.dispose()


async def test_run_rollback_twice_is_idempotent(monkeypatch):
    # EXEC-3 : a second rollback call must place ZERO unwind orders — the
    # prior unwind rows cover the fills, so the residual plan is empty.
    _install_ibinsync_stub(monkeypatch)
    from sqlalchemy import select

    from engines.execution.rollback_runner import run_rollback
    from persistence.models import StructureOrder, TradeEvent, TradeStructure

    maker, engine = await _make_session()
    try:
        struct_id, _ = await _seed_structure_with_orders(
            maker,
            configs=[
                {"side": "BUY", "qty": 5, "state": "partially_filled",
                 "qty_filled": 3, "ib_order_id": "1002"},
            ],
        )
        ib = _FakeIB()
        ib._open = [SimpleNamespace(order=SimpleNamespace(orderId=1002))]
        executor = _FakeExecutor(ib)

        first = await run_rollback(
            sessionmaker_factory=maker, executor=executor,
            structure_id=struct_id,
        )
        assert len(first["unwound"]) == 1 and first["unwound"][0]["qty"] == 3
        assert len(ib.placed) == 1

        async with maker() as db:
            struct = await db.get(TradeStructure, struct_id)
            stamp_after_first = struct.rollback_started_at
        assert stamp_after_first is not None

        second = await run_rollback(
            sessionmaker_factory=maker, executor=executor,
            structure_id=struct_id,
        )
        # Second run places NOTHING — residual is fully covered.
        assert second["unwound"] == []
        assert len(ib.placed) == 1

        async with maker() as db:
            struct = await db.get(TradeStructure, struct_id)
            unwind_rows = (await db.execute(
                select(StructureOrder)
                .where(StructureOrder.structure_id == struct_id)
                .where(StructureOrder.order_role == "unwind")
            )).scalars().all()
            events = (await db.execute(
                select(TradeEvent).where(TradeEvent.structure_id == struct_id)
            )).scalars().all()
        # Stamp set once, unchanged by the re-entry ; exactly one unwind row.
        assert struct.rollback_started_at == stamp_after_first
        assert len(unwind_rows) == 1
        assert "rollback_reentry" in {e.event_type for e in events}
    finally:
        await engine.dispose()


async def test_run_rollback_passed_plan_is_ignored(monkeypatch):
    # A stale caller-passed plan must not double-unwind : the runner always
    # recomputes from DB truth (entry legs + prior unwind rows).
    _install_ibinsync_stub(monkeypatch)
    from core.execution.rollback import RollbackPlan, UnwindAction
    from engines.execution.rollback_runner import run_rollback

    maker, engine = await _make_session()
    try:
        struct_id, _ = await _seed_structure_with_orders(
            maker,
            configs=[
                {"side": "BUY", "qty": 5, "state": "partially_filled",
                 "qty_filled": 3, "ib_order_id": "1002"},
            ],
        )
        ib = _FakeIB()
        ib._open = [SimpleNamespace(order=SimpleNamespace(orderId=1002))]
        executor = _FakeExecutor(ib)

        await run_rollback(
            sessionmaker_factory=maker, executor=executor,
            structure_id=struct_id,
        )
        assert len(ib.placed) == 1

        # Replaying the ORIGINAL (now stale) plan must not re-place.
        stale_plan = RollbackPlan(
            cancels=[],
            unwinds=[UnwindAction(leg_idx=0, side="SELL", qty=3)],
        )
        second = await run_rollback(
            sessionmaker_factory=maker, executor=executor,
            structure_id=struct_id, plan=stale_plan,
        )
        assert second["unwound"] == []
        assert len(ib.placed) == 1
    finally:
        await engine.dispose()


async def test_run_rollback_raises_when_disconnected():
    from engines.execution.rollback_runner import run_rollback

    class _Down:
        def is_connected(self):
            return False

    maker, engine = await _make_session()
    try:
        with pytest.raises(RuntimeError, match="not connected"):
            await run_rollback(
                sessionmaker_factory=maker, executor=_Down(), structure_id=999,
            )
    finally:
        await engine.dispose()


# Suppress unused-imports warnings for fixtures defined above.
_ = datetime
_ = UTC
