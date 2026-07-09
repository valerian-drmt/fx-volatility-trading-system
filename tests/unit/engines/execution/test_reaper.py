"""Unit tests for the order reaper — liveness policy (I2) + scenarios T1/T7.

Mirrors the repo convention (cf. test_order_reconciler): property-test the pure
decision, and cover the dead-feed guard on the loop entry with a stub executor
so no real DB/IB is needed.
"""
from __future__ import annotations

from core.execution.reaper_policy import REAPABLE_STATES, TERMINAL_STATES, decide_reap


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
