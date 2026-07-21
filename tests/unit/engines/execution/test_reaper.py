"""Unit tests for the order reaper — liveness policy (I2) + scenarios T1/T7.

Mirrors the repo convention (cf. test_order_reconciler): property-test the pure
decision, and cover the dead-feed guard on the loop entry with a stub executor
so no real DB/IB is needed.
"""
from __future__ import annotations

from core.execution.reaper_policy import (
    REAPABLE_STATES,
    TERMINAL_STATES,
    decide_reap,
    plan_structure_terminal_state,
)


def test_t1_stale_and_not_held_expires() -> None:
    # T1: order in flight, no fill, tau exceeded, IB does not hold it -> expired.
    for state in REAPABLE_STATES:
        assert (
            decide_reap(
                state=state, age_s=10_000, tau_s=300,
                held_at_ib=False, matches_contract=False,
            )
            == "expired"
        )


def test_stale_and_held_reconciles_filled_never_phantom() -> None:
    # held AND matches -> filled (missed fill); held but NOT matching -> expired
    # (never a phantom fill).
    assert (
        decide_reap(state="submitted", age_s=10_000, tau_s=300,
                    held_at_ib=True, matches_contract=True) == "filled"
    )
    assert (
        decide_reap(state="submitted", age_s=10_000, tau_s=300,
                    held_at_ib=True, matches_contract=False) == "expired"
    )


def test_fresh_and_terminal_orders_are_left_alone() -> None:
    assert (
        decide_reap(state="submitted", age_s=10, tau_s=300,
                    held_at_ib=False, matches_contract=False) is None
    )
    for term in TERMINAL_STATES:
        assert (
            decide_reap(state=term, age_s=1e9, tau_s=300,
                        held_at_ib=False, matches_contract=False) is None
        )


def test_live_ib_order_keys_keeps_only_working_orders() -> None:
    # A resting limit (Submitted, remaining > 0) must count as live-at-IB so the
    # reaper leaves it alone; filled/cancelled/fully-done orders must not.
    from engines.execution.reaper import live_ib_order_keys

    trades = [
        {"order_id": 67, "perm_id": 555873473, "status": "Submitted", "remaining": 10.0},
        {"order_id": 64, "perm_id": 555873472, "status": "Filled", "remaining": 0.0},
        {"order_id": 99, "perm_id": 555873499, "status": "Cancelled", "remaining": 10.0},
        {"order_id": 12, "perm_id": 555873412, "status": "Submitted", "remaining": 0.0},
        {"order_id": 20, "perm_id": 555873420, "status": "PreSubmitted", "remaining": 5.0},
    ]
    keys = live_ib_order_keys(trades)
    # working, unfilled → both ids present
    assert "67" in keys and "555873473" in keys
    assert "20" in keys and "555873420" in keys
    # filled / cancelled / fully-done → excluded
    assert "64" not in keys and "99" not in keys and "12" not in keys


def test_reaper_terminalises_structure_once_all_legs_terminal() -> None:
    # After the reaper expires a never-filled 10-delta wing, the strangle's legs
    # are e.g. [filled, expired] -> the structure must leave 'submitted', not sit
    # there forever (the ghost bug). A leg still in flight blocks terminalisation.
    assert plan_structure_terminal_state(["filled", "submitted"]) is None
    assert plan_structure_terminal_state(["filled", "expired"]) == "partial_fail"
    assert plan_structure_terminal_state(["expired", "expired"]) == "fully_failed"
    assert plan_structure_terminal_state(["filled", "filled"]) == "fully_filled"


def test_parse_order_ref() -> None:
    from engines.execution.reaper import parse_order_ref

    assert parse_order_ref("fxvol:7:42") == (7, 42)
    assert parse_order_ref(None) is None
    assert parse_order_ref("") is None
    assert parse_order_ref("something-else") is None
    assert parse_order_ref("fxvol:7") is None
    assert parse_order_ref("fxvol:7:42:9") is None
    assert parse_order_ref("fxvol:x:y") is None


async def test_orphan_with_orderref_is_adopted_not_reaped() -> None:
    # EXEC-2 residual window: a crash between placeOrder and the per-leg commit
    # leaves a live IB order whose row says pending / no ib_order_id. The sweep
    # must adopt it (fill ids + submitted_at) instead of leaving a ghost.
    from datetime import date

    from sqlalchemy import BigInteger, Integer, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from engines.execution.reaper import reap_stale_orders
    from persistence.models import Base, StructureOrder, TradeEvent, TradeStructure

    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            s = TradeStructure(
                structure_type="straddle", reference_tenor="3M",
                base_qty=5, state="submitted", execution_mode="live",
            )
            db.add(s)
            await db.flush()
            ghost = StructureOrder(
                structure_id=s.id, leg_idx=0, order_role="entry",
                contract_type="call", contract_strike=1.085,
                contract_expiry=date(2026, 9, 18),
                side="BUY", qty=5, order_type="LMT", limit_price=0.004,
                state="pending",  # never committed as submitted — the ghost
            )
            other = StructureOrder(
                structure_id=s.id, leg_idx=1, order_role="entry",
                contract_type="put", contract_strike=1.075,
                contract_expiry=date(2026, 9, 18),
                side="BUY", qty=5, order_type="LMT", limit_price=0.004,
                state="pending",  # no matching IB order → left alone
            )
            db.add_all([ghost, other])
            await db.flush()
            struct_id, ghost_id, other_id = s.id, ghost.id, other.id
            await db.commit()

        class _StubExec:
            def account_is_reporting(self) -> bool:
                return True

            async def list_all_trades(self):
                return [{
                    "order_id": 501, "perm_id": 601,
                    "status": "Submitted", "remaining": 5.0,
                    "order_ref": f"fxvol:{struct_id}:{ghost_id}",
                }]

        result = await reap_stale_orders(maker, _StubExec())
        assert result["adopted"] == [ghost_id]
        assert result["reaped"] == 0

        async with maker() as db:
            g = await db.get(StructureOrder, ghost_id)
            o = await db.get(StructureOrder, other_id)
            events = (await db.execute(
                select(TradeEvent).where(TradeEvent.structure_id == struct_id)
            )).scalars().all()
        assert g.ib_order_id == "501"
        assert g.ib_perm_id == "601"
        assert g.state == "submitted"
        assert g.submitted_at is not None
        assert o.ib_order_id is None and o.state == "pending"
        assert "order_adopted_from_ib" in {e.event_type for e in events}
    finally:
        await engine.dispose()


async def test_t7_dead_feed_is_a_no_op() -> None:
    # T7: IB feed cut (account not reporting) -> reaper acts on nothing and never
    # even opens a DB session (acting on an empty snapshot would fabricate
    # expirations).
    from engines.execution.reaper import reap_stale_orders

    class _StubExec:
        def account_is_reporting(self) -> bool:
            return False

    def _sm():  # pragma: no cover - must never be called
        raise AssertionError("session opened despite a non-reporting account")

    result = await reap_stale_orders(_sm, _StubExec())
    assert result["reaped"] == 0
    assert result.get("skipped") == "account_not_reporting"
