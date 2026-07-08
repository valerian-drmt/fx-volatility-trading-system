"""Forward position projection — pure fold of a leg's fills (invariants I3/I7).

The position of a leg is a *pure signed fold of THAT leg's executions* — nothing
else. The link execution -> order -> leg is known at fill time (the order_id is on
the IB event), so it is never reconstructed from the netted IB mirror (the lossy
back-attribution that defect D2 describes). Because the fold takes ONLY fills, the
mirror can never leak in as an attribution input (I7), and destroying and
replaying the fills reproduces the position exactly (T8).

Kept I/O-free so it is property-testable; ``persistence.projection`` wraps it to
read ``trade_fill`` and materialise ``leg_position``.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Fill:
    """One execution on a leg. ``side`` is 'BUY'/'SELL' (IB 'BOT'/'SLD' already
    normalised upstream by ``fills_handler``)."""

    side: str
    qty: float
    price: float


@dataclass(frozen=True)
class LegFold:
    open_qty: float          # signed: +buy / −sell
    avg_price: float | None  # volume-weighted over |qty|; None when no fills
    filled_qty: float        # unsigned Σ qty (ties back to order.qty_filled, I1)


def signed(side: str, qty: float) -> float:
    """+qty for a BUY, −qty for a SELL."""
    return qty if (side or "").upper() == "BUY" else -qty


def fold_fills(fills: Iterable[Fill]) -> LegFold:
    """Fold a leg's fills into its position. Deterministic, O(#fills), no I/O."""
    items = list(fills)
    open_qty = sum(signed(f.side, f.qty) for f in items)
    filled_qty = sum(f.qty for f in items)
    if filled_qty == 0:
        return LegFold(open_qty=open_qty, avg_price=None, filled_qty=filled_qty)
    notional = sum(f.qty * f.price for f in items)
    return LegFold(open_qty=open_qty, avg_price=notional / filled_qty, filled_qty=filled_qty)
