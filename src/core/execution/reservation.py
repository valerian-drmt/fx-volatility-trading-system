"""Reservation ledger — pure admission math (invariant I5, spec §8, defect D4).

The qty being closed is *reserved* on the leg so two fast close clicks can't each
see the full ``open_qty`` and both send a full close. ``available = |open_qty| −
reserved_qty`` must stay ≥ 0. This is the O(1), race-free, restart-safe form of
the stateless 409 re-sum: the guard becomes an invariant on materialised state
(``persistence.reservation`` folds it onto ``leg_position``).
"""
from __future__ import annotations


class OverReserveError(ValueError):
    """A reservation that would push reserved past |open_qty| (available < 0)."""


def available(open_qty: float, reserved_qty: float) -> float:
    """Headroom left to close: |open_qty| − reserved_qty."""
    return abs(open_qty) - reserved_qty


def try_reserve(open_qty: float, reserved_qty: float, requested: float) -> float:
    """Return the new reserved total, or raise ``OverReserveError`` if the request
    would exceed the open quantity. This is the anti-over-close guard (I5)."""
    if requested < 0:
        raise ValueError("requested must be >= 0")
    if requested > available(open_qty, reserved_qty) + 1e-9:
        raise OverReserveError(
            f"reserve {requested} exceeds available "
            f"{available(open_qty, reserved_qty)} "
            f"(open_qty={open_qty}, reserved_qty={reserved_qty})"
        )
    return reserved_qty + requested
