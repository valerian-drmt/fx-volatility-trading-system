"""Unit tests for book vs broker reconciliation (I4, scenarios T3/T7)."""
from __future__ import annotations

from core.execution.reconciliation import classify_break, compute_breaks


def test_classify_all_break_types() -> None:
    assert classify_break(5.0, 5.0) is None            # in sync
    assert classify_break(5.0, 0.0) == "missing_at_ib"     # book long, IB flat
    assert classify_break(0.0, 5.0) == "unbooked_at_ib"    # IB holds, book empty
    assert classify_break(5.0, -5.0) == "direction"        # opposite signs
    assert classify_break(5.0, 3.0) == "quantity"          # same sign, size gap


def test_rounding_noise_is_not_a_break() -> None:
    assert classify_break(5.0, 5.00005) is None            # within BREAK_EPS


def test_t3_book_equals_broker_no_break() -> None:
    # T3: two structures net to the same per-contract qty IB reports → break = 0.
    book = {"EUUQ6 P1145": 3.0, "6EU6": -2.0}
    broker = {"EUUQ6 P1145": 3.0, "6EU6": -2.0}
    assert compute_breaks(book, broker) == []


def test_compute_breaks_signed_diff() -> None:
    breaks = compute_breaks({"X": 5.0}, {"X": 3.0})
    assert len(breaks) == 1
    assert breaks[0].contract == "X"
    assert breaks[0].diff == 2.0
    assert breaks[0].break_type == "quantity"


async def test_t7_dead_feed_is_a_no_op() -> None:
    # T7: account not reporting → reconciler acts on nothing, never opens a session.
    from engines.execution.reconciler import reconcile_positions

    class _StubExec:
        def account_is_reporting(self) -> bool:
            return False

    def _sm():  # pragma: no cover - must never be called
        raise AssertionError("session opened despite a non-reporting account")

    result = await reconcile_positions(_sm, _StubExec())
    assert result["open_breaks"] == 0
    assert result.get("skipped") == "account_not_reporting"
