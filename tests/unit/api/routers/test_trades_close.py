"""Unit tests for the trade-level close planner (the anti over-close logic).

The bug this guards against: two trades holding the SAME contract net into one IB
``open_position`` attributed (most-recent-wins) to the newer trade, so closing by
that mirror qty over-closes the sibling. ``plan_trade_close`` closes each leg's
OWN filled qty, capped at the live mirror qty.
"""
from __future__ import annotations

from api.routers.trades import plan_structure_terminal_state, plan_trade_close


def test_shared_contract_closes_only_own_qty() -> None:
    # Trade holds a put (EUUX6 P1125, filled 10) + a call (EUUX6 C1170, filled 10).
    # A SIBLING trade also holds EUUX6 P1125, so IB nets it to 20 in the mirror.
    legs = [(100, "EUUX6 P1125", 10), (101, "EUUX6 C1170", 10)]
    mirror_qty = {"EUUX6 P1125": 20, "EUUX6 C1170": 10}
    plans, skips = plan_trade_close(legs, mirror_qty)

    assert skips == []
    # each leg closes its OWN 10 — NOT the netted 20 on the shared contract
    assert ("EUUX6 P1125", 10, 100) in plans
    assert ("EUUX6 C1170", 10, 101) in plans


def test_caps_at_mirror_when_less_is_available() -> None:
    # IB only shows 4 for the contract (partly closed already) → close 4, not 10.
    plans, skips = plan_trade_close([(100, "EUUX6 P1125", 10)], {"EUUX6 P1125": 4})
    assert plans == [("EUUX6 P1125", 4, 100)]
    assert skips == []


def test_skips_unfilled_and_missing_and_zero() -> None:
    legs = [
        (100, None, 0),                # unfilled — no IB contract
        (101, "EUUX6 C1170", 10),      # no live mirror row for it
        (102, "EUUX6 P1125", 10),      # mirror shows 0 → nothing to close
    ]
    plans, skips = plan_trade_close(legs, {"EUUX6 P1125": 0})
    assert plans == []
    reasons = dict(skips)
    assert "unfilled" in reasons[100]
    assert "no live mirror position" in reasons[101]
    assert "zero qty" in reasons[102]


def test_structure_terminal_state_after_cancel() -> None:
    # in flight → leave the structure as-is
    assert plan_structure_terminal_state(["filled", "submitted"]) is None
    assert plan_structure_terminal_state([]) is None
    # all legs terminal, all filled → fully_filled
    assert plan_structure_terminal_state(["filled", "filled"]) == "fully_filled"
    # some filled, rest cancelled/expired → partial_fail (a half-filled strangle)
    assert plan_structure_terminal_state(["filled", "cancelled"]) == "partial_fail"
    assert plan_structure_terminal_state(["filled", "expired"]) == "partial_fail"
    # none filled → fully_failed
    assert plan_structure_terminal_state(["cancelled", "rejected"]) == "fully_failed"
