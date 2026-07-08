"""OMS invariants I1..I7 as property tests (spec §3.2, OMS_ARCHITECTURE_CIBLE.md).

These are the assertions that must ALWAYS hold; a violated invariant is a
localised bug, not an intuition. This file is the red baseline: the invariants
already satisfied by the current code (I1 consistency, I6 exec idempotence) are
live-green and guard the good bricks; the rest are ``xfail(strict=True)`` and
each implementation phase is FORCED to flip its own marker (a strict xfail that
starts passing is reported as a failure).

Phase → invariant it turns green:
  P0 reaper              → I2   (liveness / terminalisation)
  P1 forward projection  → I3, I7
  P1 reconciliation      → I4
  P2 reservation ledger  → I5

No ``hypothesis`` dependency: property coverage is a fixed-seed loop over
randomised cases plus explicit edge cases (deterministic + reproducible).
Target modules for the not-yet-built phases are imported lazily inside the test
body so collection never errors before those modules exist.
"""
from __future__ import annotations

import random

import pytest

# ── I1 · projection consistency — order.qty_filled == Σ execution.qty ────────
# Binds to the existing pure aggregator; guards the brick we already have right.


def test_i1_qty_filled_equals_sum_of_fills() -> None:
    from core.execution.fills import FillEvent, update_order_aggregates

    rng = random.Random(11)
    for _ in range(300):
        n = rng.randint(0, 8)
        fills = [
            FillEvent(f"e{i}", rng.randint(1, 10), round(rng.uniform(0.1, 5), 4), 0.0)
            for i in range(n)
        ]
        agg = update_order_aggregates(
            fills, target_qty=100, side="BUY", preview_price=None
        )
        assert agg.qty_filled == sum(f.qty_filled for f in fills)


# ── I6 · exec idempotence — broker_exec_id UNIQUE (a fill counted once) ───────


def test_i6_exec_id_idempotent_dedup() -> None:
    from core.execution.fills import apply_fill_idempotent

    seen: set[str] = set()
    persisted: list[str] = []
    for exec_id in ["a", "b", "a", "c", "b", "a", "c"]:
        if apply_fill_idempotent(seen, exec_id):
            persisted.append(exec_id)
            seen.add(exec_id)
    assert persisted == ["a", "b", "c"]  # each fill persisted exactly once


def test_i6_schema_enforces_unique_exec_id() -> None:
    from persistence.models import StructureFill

    assert StructureFill.__table__.c.ib_execution_id.unique is True


# ── I2 · liveness — ∀ order: age > τ_max ⇒ state ∈ TERMINAL ───────────────────


def test_i2_reaper_terminalises_every_stale_order() -> None:
    from core.execution.reaper_policy import (
        REAPABLE_STATES,
        TERMINAL_STATES,
        decide_reap,
    )

    rng = random.Random(2)
    for _ in range(400):
        state = rng.choice(sorted(REAPABLE_STATES))
        held = rng.random() < 0.5
        matches = rng.random() < 0.5
        target = decide_reap(
            state=state,
            age_s=rng.uniform(301, 1e6),
            tau_s=300.0,
            held_at_ib=held,
            matches_contract=matches,
        )
        # liveness: a stale reapable order ALWAYS reaches a terminal state
        assert target in TERMINAL_STATES
        # no phantom fill: FILLED only when IB actually holds the contract
        if target == "filled":
            assert held and matches

    # not stale / not reapable → no action
    assert (
        decide_reap(
            state="submitted", age_s=10, tau_s=300.0,
            held_at_ib=False, matches_contract=False,
        )
        is None
    )
    assert (
        decide_reap(
            state="filled", age_s=1e9, tau_s=300.0,
            held_at_ib=False, matches_contract=False,
        )
        is None
    )


# ── I3 · forward attribution — position(leg) = pure signed fold of ITS fills ──


def test_i3_position_is_signed_fold_of_fills() -> None:
    from core.execution.projection import Fill, fold_fills, signed

    rng = random.Random(3)
    for _ in range(400):
        n = rng.randint(0, 10)
        fills = [
            Fill(rng.choice(["BUY", "SELL"]), rng.randint(1, 10), round(rng.uniform(0.1, 5), 4))
            for _ in range(n)
        ]
        expected = sum((f.qty if f.side == "BUY" else -f.qty) for f in fills)
        assert fold_fills(fills).open_qty == expected
        for f in fills:
            assert signed(f.side, f.qty) == (f.qty if f.side == "BUY" else -f.qty)


# ── I4 · reconciliation — every book⊖broker gap is a materialised break ───────


def test_i4_breaks_classified_and_signed() -> None:
    from core.execution.reconciliation import classify_break, compute_breaks

    assert classify_break(5.0, 5.0) is None          # in sync
    assert classify_break(5.0, 0.0) == "missing_at_ib"   # book long, IB flat
    assert classify_break(0.0, 5.0) == "unbooked_at_ib"  # IB holds, book empty
    assert classify_break(5.0, -5.0) == "direction"      # opposite signs
    assert classify_break(5.0, 3.0) == "quantity"        # same sign, size gap

    assert compute_breaks({"X": 5.0}, {"X": 5.0}) == []
    breaks = compute_breaks({"X": 5.0}, {"X": 3.0})
    assert len(breaks) == 1
    assert breaks[0].diff == 2.0


# ── I5 · non-over-close — available = |open| − reserved ≥ 0 (always) ──────────


def test_i5_available_never_negative() -> None:
    from core.execution.reservation import OverReserveError, available, try_reserve

    rng = random.Random(5)
    for _ in range(400):
        open_qty = rng.choice([-1, 1]) * rng.randint(0, 20)
        reserved = 0.0
        for _ in range(rng.randint(0, 6)):
            avail = available(open_qty, reserved)
            assert avail >= 0
            req = rng.randint(0, abs(open_qty) + 3)
            if req <= avail:
                reserved = try_reserve(open_qty, reserved, req)
                assert available(open_qty, reserved) >= 0
            else:
                with pytest.raises(OverReserveError):
                    try_reserve(open_qty, reserved, req)


# ── I7 · the mirror is never the attribution authority ───────────────────────
# The book projection takes ONLY fills — a broker/mirror qty cannot leak in as
# an input — and rebuilding from fills reproduces it exactly (T8, pure fold).


def test_i7_book_projection_is_mirror_independent() -> None:
    import inspect

    from core.execution.projection import Fill, fold_fills

    assert list(inspect.signature(fold_fills).parameters) == ["fills"]

    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(0, 10)
        fills = [
            Fill(rng.choice(["BUY", "SELL"]), rng.randint(1, 10), round(rng.uniform(0.1, 5), 4))
            for _ in range(n)
        ]
        assert fold_fills(fills).open_qty == fold_fills(list(fills)).open_qty
