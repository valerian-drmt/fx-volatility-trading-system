"""core.execution.order_states — the explicit IB status transition table (#320)."""
from __future__ import annotations

import pytest

from core.execution.order_states import IB_STATUS_TO_STATE, TERMINAL_STATES, next_state


def test_every_ib_status_ib_insync_emits_is_in_the_table() -> None:
    for s in ("PendingSubmit", "ApiPending", "PreSubmitted", "Submitted", "PendingCancel",
              "Cancelled", "ApiCancelled", "Filled", "Inactive"):
        assert s in IB_STATUS_TO_STATE


def test_unknown_status_is_reported_not_dropped() -> None:
    t = next_state("submitted", "SomethingNew")
    assert t.kind == "unknown" and t.new_state is None and "SomethingNew" in t.note
    assert next_state("submitted", None).kind == "unknown"


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
@pytest.mark.parametrize("status", ["Cancelled", "Submitted", "Inactive", "Filled"])
def test_terminal_rows_never_change_state(terminal: str, status: str) -> None:
    t = next_state(terminal, status, has_error_reason=True)
    assert t.new_state is None
    assert t.kind in ("ignored_terminal", "noop")


def test_pending_to_submitted_then_echoes_are_noops() -> None:
    assert next_state("pending", "PreSubmitted").new_state == next_state("pending", "Submitted").new_state == "submitted"
    assert next_state("pending", "Submitted").new_state == "submitted"
    assert next_state("submitted", "Submitted").kind == "noop"
    assert next_state("partially_filled", "Submitted").kind == "noop"
    assert next_state("submitted", "PendingCancel").kind == "noop"   # was unmapped (EX-06)


def test_filled_status_alone_does_not_fill() -> None:
    t = next_state("submitted", "Filled")
    assert t.new_state is None and t.kind == "fill_pending"


def test_inactive_is_a_hold_without_an_error_and_a_reject_with_one() -> None:
    held = next_state("submitted", "Inactive")
    assert held.new_state is None and held.kind == "held"
    rejected = next_state("submitted", "Inactive", has_error_reason=True)
    assert rejected.new_state == "rejected" and rejected.kind == "advance"


def test_cancelled_with_an_ib_error_is_a_reject_without_one_a_cancel() -> None:
    assert next_state("submitted", "Cancelled").new_state == "cancelled"
    assert next_state("submitted", "ApiCancelled").new_state == "cancelled"
    assert next_state("pending", "Cancelled", has_error_reason=True).new_state == "rejected"
