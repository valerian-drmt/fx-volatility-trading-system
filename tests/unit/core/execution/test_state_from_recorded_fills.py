"""Unit tests for book-authoritative order-state reconciliation.

The reconciler must repair a stuck order's state from its OWN recorded fills, not
the netted IB mirror — when two trades hold opposite sides of one contract, IB
nets to zero and the mirror can't confirm a fill that really happened. It must
NEVER invent a fill (no recorded fills → leave it).
"""
from __future__ import annotations

from core.execution.fills import state_from_recorded_fills


def test_recorded_fills_cover_qty_but_state_stuck_becomes_filled() -> None:
    # The '10/10 still submitted' case: fills recorded, state never advanced.
    assert state_from_recorded_fills(10, 10, "submitted") == "filled"
    assert state_from_recorded_fills(10, 10, "partially_filled") == "filled"
    assert state_from_recorded_fills(12, 10, "acknowledged") == "filled"  # over-covered


def test_partial_recorded_but_still_submitted_becomes_partially_filled() -> None:
    assert state_from_recorded_fills(4, 10, "submitted") == "partially_filled"


def test_no_recorded_fills_never_invents_a_fill() -> None:
    # The netted-mirror trap: zero recorded → we have NO evidence → leave it alone.
    assert state_from_recorded_fills(0, 10, "submitted") is None
    assert state_from_recorded_fills(-1, 10, "submitted") is None


def test_already_filled_or_consistent_partial_is_left_alone() -> None:
    # Already terminal-filled → nothing to do.
    assert state_from_recorded_fills(10, 10, "filled") is None
    # Partial already reflected as partially_filled → no spurious flip.
    assert state_from_recorded_fills(4, 10, "partially_filled") is None
