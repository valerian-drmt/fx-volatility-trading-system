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
