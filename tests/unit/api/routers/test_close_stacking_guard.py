"""Unit tests for the close-stacking guard's in-flight accounting.

The guard refuses to stack a new close when live closing orders already cover the
open qty (so re-clicking Close during IB's ~30 s fill lag can't over-close). It
must SELF-HEAL: a close that's stuck — cancelled at IB without its DB row flipped
terminal, or CREATED BUT NEVER DISPATCHED (``submitted_at IS NULL``) — must stop
counting after the window, else it locks the position forever. That last case is
the bug behind "position #31 already has 1 contract(s) closing — refusing …".
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.routers.positions import close_inflight_remaining

_NOW = datetime(2026, 7, 9, 15, 0, tzinfo=UTC)
_WINDOW = timedelta(minutes=3)


def _rem(**kw: object) -> int:
    base: dict[str, object] = {
        "qty": 8, "qty_filled": 0, "submitted_at": _NOW, "state_updated_at": _NOW,
    }
    base.update(kw)
    state = base.pop("state", None)
    return close_inflight_remaining(
        base["qty"], base["qty_filled"], base["submitted_at"],  # type: ignore[arg-type]
        base["state_updated_at"], _NOW, _WINDOW, state,  # type: ignore[arg-type]
    )


def test_fresh_dispatched_close_counts_full_remaining() -> None:
    # just submitted, nothing filled → all 8 count as in flight
    assert _rem(submitted_at=_NOW) == 8
    # partially filled → only the unfilled remainder counts
    assert _rem(qty=8, qty_filled=3, submitted_at=_NOW) == 5


def test_a_dispatched_close_counts_for_as_long_as_it_can_fill() -> None:
    """#486. Prod, 2026-11-12: a SELL 100 resting at IB for more than three
    minutes stopped counting, and a second SELL 100 was accepted on a long of
    100. An order that was dispatched ends by a fill, a cancel or the reaper,
    never by getting old."""
    old = _NOW - timedelta(minutes=10)
    assert _rem(submitted_at=old, state_updated_at=old, state="submitted") == 8
    assert _rem(submitted_at=old, state_updated_at=old, state="partially_filled", qty_filled=3) == 5
    days = _NOW - timedelta(days=2)
    assert _rem(submitted_at=days, state_updated_at=days, state="submitted") == 8


def test_never_dispatched_close_counts_only_within_window() -> None:
    # submitted_at IS NULL (engine was down): fresh creation → still counts …
    assert _rem(submitted_at=None, state_updated_at=_NOW, state="pending") == 8
    # … but once older than the window it self-heals — the permanent-lock bug fix.
    # Only this case ages out: the order never reached the broker.
    old = _NOW - timedelta(minutes=10)
    assert _rem(submitted_at=None, state_updated_at=old, state="pending") == 0
    # both timestamps missing → nothing to age it against → still counts (safe)
    assert _rem(submitted_at=None, state_updated_at=None, state="pending") == 8
