"""Close-reservation ledger math — pure (no I/O). Invariant I5, spec §8.

A close in flight RESERVES quantity on the leg it targets :

    available = |open_qty| − reserved_qty      (must stay ≥ 0)

Reserving beyond ``available`` is refused by construction — the double-click
over-close (root defect D4) becomes impossible, not merely guarded by a
stateless re-sum. The DB layer materialises ``reserved_qty`` on
``leg_position`` (survives restarts, race-free via a conditional UPDATE) ;
this module owns the arithmetic so it is testable without a DB.

Lifecycle (spec §8) :
    reserve on close creation           reserved += q      (guarded here)
    release on fill                     reserved −= qf     (open shrinks too)
    release on terminalisation          reserved −= (q − qty_filled)
The steady-state identity : reserved(leg) == Σ (qty − qty_filled) over
non-terminal closing orders targeting the leg — a pure fold, recomputable.
"""
from __future__ import annotations


class OverReserveError(ValueError):
    """Requested close quantity exceeds the leg's available quantity."""


def available(*, open_qty: float, reserved_qty: float) -> float:
    """Quantity still closeable on the leg : |open| − reserved."""
    return abs(open_qty) - reserved_qty


def try_reserve(*, open_qty: float, reserved_qty: float, requested: float) -> float:
    """Reserve ``requested`` more ; returns the NEW reserved total.

    Raises :class:`OverReserveError` when the request exceeds availability —
    the invariant ``available ≥ 0`` can never be violated through here.
    """
    if requested <= 0:
        raise OverReserveError(f"requested must be > 0, got {requested}")
    avail = available(open_qty=open_qty, reserved_qty=reserved_qty)
    if requested > avail:
        raise OverReserveError(
            f"requested {requested} exceeds available {avail} "
            f"(open {open_qty}, reserved {reserved_qty})"
        )
    return reserved_qty + requested


def outstanding_reservation(closing_orders: list[tuple[float, float, str]],
                            *, terminal_states: frozenset[str]) -> float:
    """The fold : Σ (qty − qty_filled) over non-terminal closing orders.

    ``closing_orders`` is a list of ``(qty, qty_filled, state)`` tuples for
    the closing orders targeting one leg. This is the steady-state value
    ``leg_position.reserved_qty`` converges to after every event — creation
    reserves, fills release the filled part, terminalisation releases the
    residual.
    """
    return sum(
        max(qty - qty_filled, 0.0)
        for qty, qty_filled, state in closing_orders
        if state not in terminal_states
    )
