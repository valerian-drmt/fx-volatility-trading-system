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


# ── Netting (#462) ────────────────────────────────────────────────────────────
# A leg's position is the fold of its OWN executions and of every execution
# that the book attributes to it: the fills of the closing orders that name it
# (``closes_order_id``) and its lifecycle events (expiry, exercise, repair).
# The link is still known at fill time and still never comes from the IB
# mirror (I7); what changed is that a close now reduces the leg it closes
# instead of living for ever as an opposite position on its own row.


@dataclass(frozen=True)
class BookEntry:
    """One execution or lifecycle event applied to a leg, in execution order."""

    side: str      # 'BUY' / 'SELL'
    qty: float     # magnitude
    price: float
    own: bool = True   # False: a close or an event attributed to this leg


@dataclass(frozen=True)
class NetLeg:
    open_qty: float            # signed, after closes and events
    avg_price: float | None    # avg cost of what is still open; entry VWAP when flat
    realized_pnl_usd: float    # on the quantity closed, at ``multiplier``


def net_leg(entries: Iterable[BookEntry], *, multiplier: float) -> NetLeg:
    """Average-cost fold of everything attributed to one leg. Pure."""
    from core.ledger import LedgerFill
    from core.ledger import fold_fills as ledger_fold

    items = [e for e in entries if e.qty > 0]
    if not items:
        return NetLeg(open_qty=0.0, avg_price=None, realized_pnl_usd=0.0)
    led = ledger_fold([
        LedgerFill(contract="leg", side=(e.side or "").upper(), qty=e.qty, price=e.price,
                   commission=0.0, multiplier=multiplier)
        for e in items
    ])["leg"]
    own = fold_fills(Fill(side=e.side, qty=e.qty, price=e.price) for e in items if e.own)
    avg = led.avg_cost if led.net_qty != 0 else own.avg_price
    return NetLeg(open_qty=led.net_qty, avg_price=avg, realized_pnl_usd=led.realized_pnl)
