"""Unit tests for core.execution.rollback."""
from __future__ import annotations

from core.execution.rollback import OrderState, UnwindState, decide_rollback


def _o(leg_idx: int, state: str, side: str = "BUY", qty: int = 10, qty_filled: int = 0) -> OrderState:
    return OrderState(leg_idx=leg_idx, state=state, side=side, qty=qty, qty_filled=qty_filled)


def _u(leg_idx: int, state: str, qty: int, qty_filled: int = 0) -> UnwindState:
    return UnwindState(leg_idx=leg_idx, state=state, qty=qty, qty_filled=qty_filled)


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


# --- EXEC-3 : residual-based unwinds (idempotent second rollback) -----------

def test_existing_full_unwind_means_no_action():
    # filled leg qty_filled=3 with an existing submitted unwind qty=3 → no action
    plan = decide_rollback(
        [_o(0, "filled", side="BUY", qty=3, qty_filled=3)],
        [_u(0, "submitted", qty=3)],
    )
    assert plan.unwinds == []


def test_partial_prior_unwind_leaves_residual():
    # qty_filled=3, existing unwind qty=2 (partial prior rollback) → unwind 1
    plan = decide_rollback(
        [_o(0, "filled", side="BUY", qty=3, qty_filled=3)],
        [_u(0, "submitted", qty=2)],
    )
    assert len(plan.unwinds) == 1
    assert plan.unwinds[0].qty == 1 and plan.unwinds[0].side == "SELL"


def test_failed_prior_unwind_covers_nothing():
    # existing unwind in 'rejected' → full re-unwind qty=3 (same for
    # cancelled / expired).
    for failed_state in ("rejected", "cancelled", "expired"):
        plan = decide_rollback(
            [_o(0, "filled", side="BUY", qty=3, qty_filled=3)],
            [_u(0, failed_state, qty=3)],
        )
        assert len(plan.unwinds) == 1
        assert plan.unwinds[0].qty == 3


def test_multi_leg_residuals_are_per_leg():
    plan = decide_rollback(
        [
            _o(0, "filled", side="BUY", qty=5, qty_filled=5),      # covered 5/5
            _o(1, "partially_filled", side="SELL", qty=5, qty_filled=4),  # covered 2/4
            _o(2, "filled", side="BUY", qty=5, qty_filled=5),      # uncovered
        ],
        [
            _u(0, "filled", qty=5),
            _u(1, "submitted", qty=2),
        ],
    )
    by_leg = {u.leg_idx: u for u in plan.unwinds}
    assert 0 not in by_leg
    assert by_leg[1].qty == 2 and by_leg[1].side == "BUY"
    assert by_leg[2].qty == 5 and by_leg[2].side == "SELL"


def test_multiple_prior_unwinds_on_one_leg_accumulate():
    plan = decide_rollback(
        [_o(0, "filled", side="BUY", qty=6, qty_filled=6)],
        [_u(0, "filled", qty=2), _u(0, "submitted", qty=3)],
    )
    assert len(plan.unwinds) == 1
    assert plan.unwinds[0].qty == 1
