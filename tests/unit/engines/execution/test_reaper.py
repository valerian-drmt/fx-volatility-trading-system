"""Unit tests for engines.execution.reaper — order FSM liveness (invariant I2).

Scenario coverage from OMS_ARCHITECTURE_CIBLE.md §13 :
  * T1 — stale working order, no fill, not at IB, contract not held → expired.
  * T2 — stale order IB actually filled : real executions replayed when IB
    still reports them ; audited held-contract backfill when it does not ;
    NEVER a fill when the contract is absent (no phantom).
  * T7 — feed dead (account not reporting) → the reaper does not act.

Uses an in-memory aiosqlite DB ; the IB view is a duck-typed stub executor.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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


class _StubExecutor:
    """Duck-typed IB view consumed by the reaper."""

    def __init__(self, *, reporting: bool = True,
                 live_order_ids: tuple[str, ...] = (),
                 held: dict[str, float] | None = None,
                 fills: list | None = None) -> None:
        self._reporting = reporting
        self._live = {str(i) for i in live_order_ids}
        self._held = dict(held or {})
        self._fills = list(fills or [])

    def account_is_reporting(self) -> bool:
        return self._reporting

    async def is_order_live(self, ib_order_id) -> bool:
        return ib_order_id is not None and str(ib_order_id) in self._live

    async def held_contracts(self) -> dict[str, float]:
        return dict(self._held)

    async def recent_fills(self) -> list:
        return list(self._fills)


def _ib_fill(*, exec_id: str, ib_order_id: int, qty: int, price: float,
             side: str = "BOT") -> SimpleNamespace:
    return SimpleNamespace(
        execution=SimpleNamespace(
            execId=exec_id, orderId=ib_order_id, shares=qty, price=price,
            side=side, time=datetime.now(UTC), exchange="CME",
        ),
        commissionReport=SimpleNamespace(commission=1.0),
    )


async def _seed_order(maker, *, qty: int = 10, side: str = "BUY",
                      state: str = "submitted", contract_type: str = "call",
                      strike: float | None = 1.13,
                      local_symbol: str | None = None,
                      age_s: float = 3600.0,
                      ib_order_id: str | None = "901",
                      qty_filled: int = 0) -> tuple[int, int]:
    from persistence.models import StructureOrder, TradeStructure
    async with maker() as db:
        s = TradeStructure(structure_type="strangle", reference_tenor="3M",
                           base_qty=qty, state="submitted", execution_mode="live")
        db.add(s)
        await db.flush()
        o = StructureOrder(
            structure_id=s.id, leg_idx=0, order_role="entry",
            contract_type=contract_type, contract_strike=strike,
            side=side, qty=qty, qty_filled=qty_filled, order_type="LMT",
            limit_price=1.234, preview_price=1.23, state=state,
            ib_order_id=ib_order_id, ib_local_symbol=local_symbol,
            submitted_at=datetime.now(UTC) - timedelta(seconds=age_s),
        )
        db.add(o)
        await db.flush()
        sid, oid = s.id, o.id
        await db.commit()
        return sid, oid


async def _get_order(maker, oid: int):
    from persistence.models import StructureOrder
    async with maker() as db:
        return await db.get(StructureOrder, oid)


async def _reap(maker, executor, tau: float = 300.0) -> int:
    from engines.execution.reaper import reap_stale_orders
    return await reap_stale_orders(
        sessionmaker_factory=maker, executor=executor, tau_stale_s=tau,
    )


# ── T1 : stale, unfilled, absent at IB, contract not held → expired ──────────

async def test_t1_stale_unfilled_order_expires_and_is_audited():
    from sqlalchemy import select

    from persistence.models import TradeEvent

    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker)
        n = await _reap(maker, _StubExecutor())
        order = await _get_order(maker, oid)
        assert n == 1
        assert order.state == "expired"
        async with maker() as db:
            events = (await db.execute(
                select(TradeEvent).where(TradeEvent.order_id == oid)
            )).scalars().all()
        assert [e.event_type for e in events] == ["order_expired_by_reaper"]

        # Idempotent : a second pass finds nothing to do.
        assert await _reap(maker, _StubExecutor()) == 0
    finally:
        await engine.dispose()


async def test_fresh_order_is_left_alone():
    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker, age_s=60.0)   # younger than τ
        assert await _reap(maker, _StubExecutor(), tau=300.0) == 0
        assert (await _get_order(maker, oid)).state == "submitted"
    finally:
        await engine.dispose()


async def test_order_genuinely_resting_at_ib_is_left_alone():
    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker)
        n = await _reap(maker, _StubExecutor(live_order_ids=("901",)))
        assert n == 0
        assert (await _get_order(maker, oid)).state == "submitted"
    finally:
        await engine.dispose()


# ── T2 : missed fill — replay real executions, else audited backfill ─────────

async def test_t2_missed_fill_replays_real_executions():
    from sqlalchemy import select

    from persistence.models import StructureFill

    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker, qty=10)
        stub = _StubExecutor(
            held={"EUUV6 C1130": 10.0},
            fills=[_ib_fill(exec_id="lost-1", ib_order_id=901, qty=10, price=1.21)],
        )
        n = await _reap(maker, stub)
        order = await _get_order(maker, oid)
        assert n == 1
        assert order.state == "filled"
        assert order.qty_filled == 10           # I1 holds : real rows, not synthetic
        async with maker() as db:
            rows = (await db.execute(
                select(StructureFill).where(StructureFill.order_id == oid)
            )).scalars().all()
        assert len(rows) == 1
        assert rows[0].ib_execution_id == "lost-1"
    finally:
        await engine.dispose()


async def test_t2_contract_held_but_executions_gone_backfills_with_audit():
    from sqlalchemy import select

    from persistence.models import StructureFill, TradeEvent

    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker, qty=10, side="BUY",
                                      contract_type="call", strike=1.13)
        n = await _reap(maker, _StubExecutor(held={"EUUV6 C1130": 10.0}))
        order = await _get_order(maker, oid)
        assert n == 1
        assert order.state == "filled"
        assert order.ib_local_symbol == "EUUV6 C1130"
        async with maker() as db:
            fills = (await db.execute(select(StructureFill))).scalars().all()
            events = (await db.execute(
                select(TradeEvent).where(TradeEvent.order_id == oid)
            )).scalars().all()
        assert fills == []                       # no synthetic execution rows
        assert [e.event_type for e in events] == ["order_filled_from_ib_position"]
    finally:
        await engine.dispose()


async def test_t2_never_phantom_on_direction_mismatch():
    """Contract held but net SHORT while the stale order was a BUY : ambiguous
    (another structure dominates the net) → expire, never fill."""
    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker, side="BUY", contract_type="call",
                                      strike=1.13)
        n = await _reap(maker, _StubExecutor(held={"EUUV6 C1130": -4.0}))
        order = await _get_order(maker, oid)
        assert n == 1
        assert order.state == "expired"
        assert order.qty_filled == 0
    finally:
        await engine.dispose()


async def test_partial_residual_expires_without_touching_real_aggregates():
    """T5 (reaper half) : a 7/17 partial whose residual died at IB expires ;
    qty_filled stays at the real 7 — the backfill never overwrites it."""
    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker, qty=17, qty_filled=7,
                                      state="partially_filled",
                                      local_symbol="EUUV6 C1130")
        n = await _reap(maker, _StubExecutor(held={"EUUV6 C1130": 7.0}))
        order = await _get_order(maker, oid)
        assert n == 1
        assert order.state == "expired"
        assert order.qty_filled == 7
    finally:
        await engine.dispose()


# ── T7 : dead feed — the reaper must not act on an empty snapshot ────────────

async def test_t7_reaper_noops_when_account_not_reporting():
    maker, engine = await _make_session()
    try:
        _sid, oid = await _seed_order(maker)
        n = await _reap(maker, _StubExecutor(reporting=False))
        assert n == 0
        assert (await _get_order(maker, oid)).state == "submitted"
    finally:
        await engine.dispose()
