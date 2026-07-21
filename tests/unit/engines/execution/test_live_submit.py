"""Unit tests for engines.execution.live_submit (EXEC-1 / EXEC-2).

EXEC-1 — idempotent live submit:
  * an atomic submit claim means a replayed call places ZERO extra orders;
  * legs already carrying an ib_order_id are never re-placed;
  * every IB order is stamped with the durable idempotency key
    ``orderRef='fxvol:{structure_id}:{order_id}'``.

EXEC-2 — DB never diverges from IB mid-submit:
  * phase 1 qualifies EVERY leg before placing ANY (a qualification failure
    places zero orders and releases the claim so a retry is allowed);
  * phase 2 commits PER LEG — a failure at leg k leaves legs 1..k-1 committed
    with their ib_order_id, and the structure marked partial_fail.

`ib_insync` is not imported ; we monkey-patch a stub module (same convention
as test_rollback_runner.py).
"""
from __future__ import annotations

import sys
from datetime import date
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
            self.orderRef = ""

    class _MarketOrder:
        def __init__(self, action, totalQuantity):
            self.action = action
            self.totalQuantity = totalQuantity
            self.tif = "DAY"
            self.orderRef = ""

    class _ComboLeg:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    fake_mod.Contract = _Contract
    fake_mod.LimitOrder = _LimitOrder
    fake_mod.MarketOrder = _MarketOrder
    fake_mod.ComboLeg = _ComboLeg
    monkeypatch.setitem(sys.modules, "ib_insync", fake_mod)


class _Slot:
    def __init__(self):
        self._listeners = []

    def __iadd__(self, fn):
        self._listeners.append(fn)
        return self


class _FakeTrade:
    def __init__(self, *, order_id: int, order):
        self.order = SimpleNamespace(
            orderId=order_id, permId=order_id * 10,
            orderRef=getattr(order, "orderRef", ""),
        )
        self.statusEvent = _Slot()
        self.fillEvent = _Slot()
        self.filledEvent = _Slot()
        self.cancelledEvent = _Slot()


class _FakeIB:
    """Scripted IB stub. ``fail_qualify_after`` / ``fail_place_after`` make the
    N-th (0-based) qualify/place call fail, to script mid-submit failures."""

    def __init__(self, *, fail_qualify_after: int | None = None,
                 fail_place_after: int | None = None):
        self.placed: list[SimpleNamespace] = []
        self.qualify_calls = 0
        self._fail_qualify_after = fail_qualify_after
        self._fail_place_after = fail_place_after
        self._next_order_id = 500

    async def qualifyContractsAsync(self, contract):
        idx = self.qualify_calls
        self.qualify_calls += 1
        if self._fail_qualify_after is not None and idx >= self._fail_qualify_after:
            return []
        contract.conId = 9000 + idx
        contract.localSymbol = "EUR-STUB"
        contract.tradingClass = "EUU"
        return [contract]

    async def reqTickersAsync(self, contract):
        return [SimpleNamespace(bid=1.01, ask=1.02, marketPrice=lambda: 1.015)]

    def placeOrder(self, contract, order):
        if (self._fail_place_after is not None
                and len(self.placed) >= self._fail_place_after):
            raise RuntimeError("scripted placeOrder failure")
        self.placed.append(SimpleNamespace(contract=contract, order=order))
        self._next_order_id += 1
        return _FakeTrade(order_id=self._next_order_id, order=order)


class _FakeExecutor:
    def __init__(self, ib):
        self._ib = ib

    def is_connected(self) -> bool:
        return True

    def _ensure(self):
        return self._ib


# --- Seeding ----------------------------------------------------------------

async def _seed(maker, *, struct_state="submitted", legs=2, leg_overrides=None):
    from persistence.models import StructureOrder, TradeStructure
    leg_overrides = leg_overrides or {}
    async with maker() as db:
        s = TradeStructure(
            structure_type="strangle", reference_tenor="3M",
            base_qty=5, state=struct_state, execution_mode="live",
        )
        db.add(s)
        await db.flush()
        order_ids = []
        for i in range(legs):
            cfg = leg_overrides.get(i, {})
            o = StructureOrder(
                structure_id=s.id, leg_idx=i, order_role="entry",
                contract_type="call", contract_strike=1.08 + i * 0.01,
                contract_expiry=date(2026, 9, 18),
                side="BUY", qty=5,
                order_type="LMT", limit_price=0.0042,
                preview_price=0.0042,
                state=cfg.get("state", "pending"),
                ib_order_id=cfg.get("ib_order_id"),
            )
            db.add(o)
            await db.flush()
            order_ids.append(o.id)
        await db.commit()
        return s.id, order_ids


async def _get_struct(maker, struct_id):
    from persistence.models import TradeStructure
    async with maker() as db:
        return await db.get(TradeStructure, struct_id)


async def _get_orders(maker, struct_id):
    from sqlalchemy import select

    from persistence.models import StructureOrder
    async with maker() as db:
        return (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.structure_id == struct_id)
            .order_by(StructureOrder.leg_idx)
        )).scalars().all()


async def _get_events(maker, struct_id):
    from sqlalchemy import select

    from persistence.models import TradeEvent
    async with maker() as db:
        return (await db.execute(
            select(TradeEvent).where(TradeEvent.structure_id == struct_id)
        )).scalars().all()


# --- EXEC-1 : idempotency ---------------------------------------------------

async def test_double_submit_places_each_leg_exactly_once(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.live_submit import (
        LiveSubmitAlreadyClaimed,
        submit_structure_live,
    )

    maker, engine = await _make_session()
    try:
        struct_id, order_ids = await _seed(maker, legs=2)
        ib = _FakeIB()
        executor = _FakeExecutor(ib)

        result = await submit_structure_live(
            sessionmaker_factory=maker, executor=executor,
            structure_id=struct_id,
        )
        assert result["n_orders_placed"] == 2

        # Replay (API retry / double-click) → refused by the claim, and the
        # fake IB has seen exactly 2 placements total across both calls.
        with pytest.raises(LiveSubmitAlreadyClaimed):
            await submit_structure_live(
                sessionmaker_factory=maker, executor=executor,
                structure_id=struct_id,
            )
        assert len(ib.placed) == 2

        struct = await _get_struct(maker, struct_id)
        assert struct.submit_claimed_at is not None
    finally:
        await engine.dispose()


async def test_orderref_stamped_on_every_leg(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.live_submit import submit_structure_live

    maker, engine = await _make_session()
    try:
        struct_id, order_ids = await _seed(maker, legs=2)
        ib = _FakeIB()
        await submit_structure_live(
            sessionmaker_factory=maker, executor=_FakeExecutor(ib),
            structure_id=struct_id,
        )
        refs = [p.order.orderRef for p in ib.placed]
        assert refs == [f"fxvol:{struct_id}:{oid}" for oid in order_ids]
    finally:
        await engine.dispose()


async def test_legs_with_existing_ib_order_id_are_skipped(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.live_submit import submit_structure_live

    maker, engine = await _make_session()
    try:
        struct_id, order_ids = await _seed(
            maker, legs=2,
            leg_overrides={0: {"state": "submitted", "ib_order_id": "777"}},
        )
        ib = _FakeIB()
        result = await submit_structure_live(
            sessionmaker_factory=maker, executor=_FakeExecutor(ib),
            structure_id=struct_id,
        )
        assert result["n_orders_placed"] == 1
        assert len(ib.placed) == 1
        assert ib.placed[0].order.orderRef == f"fxvol:{struct_id}:{order_ids[1]}"
    finally:
        await engine.dispose()


async def test_all_legs_placed_is_idempotent_noop(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.live_submit import submit_structure_live

    maker, engine = await _make_session()
    try:
        struct_id, _ = await _seed(
            maker, legs=2,
            leg_overrides={
                0: {"state": "submitted", "ib_order_id": "777"},
                1: {"state": "submitted", "ib_order_id": "778"},
            },
        )
        ib = _FakeIB()
        result = await submit_structure_live(
            sessionmaker_factory=maker, executor=_FakeExecutor(ib),
            structure_id=struct_id,
        )
        assert result["noop"] is True
        assert result["n_orders_placed"] == 0
        assert ib.placed == []
    finally:
        await engine.dispose()


async def test_refuses_structure_not_in_submitted_state(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.live_submit import LiveSubmitError, submit_structure_live

    maker, engine = await _make_session()
    try:
        struct_id, _ = await _seed(maker, struct_state="partial_fill", legs=1)
        ib = _FakeIB()
        with pytest.raises(LiveSubmitError, match="only allowed from 'submitted'"):
            await submit_structure_live(
                sessionmaker_factory=maker, executor=_FakeExecutor(ib),
                structure_id=struct_id,
            )
        assert ib.placed == []
        # No claim taken — the refusal happened before the claim.
        struct = await _get_struct(maker, struct_id)
        assert struct.submit_claimed_at is None
    finally:
        await engine.dispose()


# --- EXEC-2 : qualify-all-first + per-leg commit ----------------------------

async def test_qualification_failure_places_zero_orders_and_releases_claim(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.live_submit import LiveSubmitError, submit_structure_live

    maker, engine = await _make_session()
    try:
        struct_id, _ = await _seed(maker, legs=2)
        # Leg 2's qualification fails — phase-1 ordering means leg 1 must NOT
        # have been placed either.
        ib = _FakeIB(fail_qualify_after=1)
        with pytest.raises(LiveSubmitError, match="not qualified"):
            await submit_structure_live(
                sessionmaker_factory=maker, executor=_FakeExecutor(ib),
                structure_id=struct_id,
            )
        assert ib.placed == []

        # Claim released → a genuine retry is allowed and places both legs.
        struct = await _get_struct(maker, struct_id)
        assert struct.submit_claimed_at is None
        events = await _get_events(maker, struct_id)
        assert "live_submit_aborted" in {e.event_type for e in events}

        ib2 = _FakeIB()
        result = await submit_structure_live(
            sessionmaker_factory=maker, executor=_FakeExecutor(ib2),
            structure_id=struct_id,
        )
        assert result["n_orders_placed"] == 2
    finally:
        await engine.dispose()


async def test_mid_submit_place_failure_keeps_placed_leg_committed(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from engines.execution.live_submit import LiveSubmitError, submit_structure_live

    maker, engine = await _make_session()
    try:
        struct_id, order_ids = await _seed(maker, legs=2)
        ib = _FakeIB(fail_place_after=1)  # leg 1 places, leg 2 raises
        with pytest.raises(LiveSubmitError, match="failed after 1/2"):
            await submit_structure_live(
                sessionmaker_factory=maker, executor=_FakeExecutor(ib),
                structure_id=struct_id,
            )

        rows = await _get_orders(maker, struct_id)
        # Leg 0 : placed AND durably committed — visible to reaper/watchdog.
        assert rows[0].ib_order_id is not None
        assert rows[0].state == "submitted"
        assert rows[0].submitted_at is not None
        # Leg 1 : never placed, still pending with no ib_order_id.
        assert rows[1].ib_order_id is None
        assert rows[1].state == "pending"

        struct = await _get_struct(maker, struct_id)
        assert struct.state == "partial_fail"
        # Claim stays: a blind retry after a partial failure must NOT re-place.
        assert struct.submit_claimed_at is not None
        events = await _get_events(maker, struct_id)
        assert "live_submit_partial_failure" in {e.event_type for e in events}
    finally:
        await engine.dispose()


async def test_happy_path_commits_once_per_leg(monkeypatch):
    _install_ibinsync_stub(monkeypatch)
    from sqlalchemy.ext.asyncio import AsyncSession

    from engines.execution.live_submit import submit_structure_live

    maker, engine = await _make_session()
    try:
        struct_id, _ = await _seed(maker, legs=3)

        commits: list[int] = []
        orig_commit = AsyncSession.commit

        async def counting_commit(self):
            commits.append(1)
            await orig_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", counting_commit)
        ib = _FakeIB()
        result = await submit_structure_live(
            sessionmaker_factory=maker, executor=_FakeExecutor(ib),
            structure_id=struct_id,
        )
        assert result["n_orders_placed"] == 3
        # 1 claim commit + 1 commit per leg — locks the per-leg-commit property.
        assert len(commits) == 1 + 3

        rows = await _get_orders(maker, struct_id)
        assert all(r.ib_order_id is not None and r.state == "submitted" for r in rows)
    finally:
        await engine.dispose()


async def test_raises_when_disconnected():
    from engines.execution.live_submit import LiveSubmitError, submit_structure_live

    class _Down:
        def is_connected(self):
            return False

    maker, engine = await _make_session()
    try:
        with pytest.raises(LiveSubmitError, match="not connected"):
            await submit_structure_live(
                sessionmaker_factory=maker, executor=_Down(), structure_id=1,
            )
    finally:
        await engine.dispose()
