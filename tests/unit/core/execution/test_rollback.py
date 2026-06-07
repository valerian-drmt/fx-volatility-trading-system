"""Unit tests for core.execution.rollback."""
from __future__ import annotations

from core.execution.rollback import OrderState, decide_rollback


def _o(leg_idx: int, state: str, side: str = "BUY", qty: int = 10, qty_filled: int = 0) -> OrderState:
    return OrderState(leg_idx=leg_idx, state=state, side=side, qty=qty, qty_filled=qty_filled)


def test_all_pending_just_cancels():
    plan = decide_rollback([
        _o(0, "submitted"),
        _o(1, "submitted"),
    ])
    assert {c.leg_idx for c in plan.cancels} == {0, 1}
    assert plan.unwinds == []


def test_partial_fill_unwind_opposite_side():
    plan = decide_rollback([
        _o(0, "partially_filled", side="BUY", qty=10, qty_filled=4),
        _o(1, "submitted", side="SELL", qty=10, qty_filled=0),
    ])
    assert any(c.leg_idx == 0 for c in plan.cancels)
    assert any(c.leg_idx == 1 for c in plan.cancels)
    assert len(plan.unwinds) == 1
    u = plan.unwinds[0]
    assert u.leg_idx == 0 and u.side == "SELL" and u.qty == 4


def test_filled_leg_also_unwound():
    """A leg fully filled when sibling is rejected → flatten the naked exposure."""
    plan = decide_rollback([
        _o(0, "filled", side="BUY", qty=10, qty_filled=10),
        _o(1, "rejected", side="BUY", qty=10, qty_filled=0),
    ])
    assert plan.cancels == []  # filled and rejected aren't cancellable
    assert len(plan.unwinds) == 1
    assert plan.unwinds[0].side == "SELL" and plan.unwinds[0].qty == 10


def test_rejected_no_cancel_no_unwind():
    plan = decide_rollback([_o(0, "rejected")])
    assert plan.is_noop()


def test_cancelled_no_op():
    plan = decide_rollback([_o(0, "cancelled")])
    assert plan.is_noop()


def test_empty_input_noop():
    assert decide_rollback([]).is_noop()


def test_acknowledged_state_cancellable():
    plan = decide_rollback([_o(0, "acknowledged")])
    assert len(plan.cancels) == 1


def test_lowercase_side_ok():
    plan = decide_rollback([_o(0, "partially_filled", side="buy", qty=5, qty_filled=2)])
    assert plan.unwinds[0].side == "SELL"
