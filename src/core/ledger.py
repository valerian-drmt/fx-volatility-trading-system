"""Position ledger — positions and realised P&L folded from the append-only
``trade_fill`` event log, average-cost method. Pure (no I/O).

This is the *audit-grade* answer to "what do we hold and what did we make":
reproducible from the immutable fill events alone, independent of the mutable IB
mirror (`open_position`). The mirror answers "what does the broker say right now";
this answers "what do our own executions add up to" — and the two should agree
(that's exactly what /positions/reconciliation checks).

Average-cost convention (standard for option premium):
  - buying to open / adding raises the average cost of the open position;
  - selling (for a long) or buying-back (for a short) *realises* P&L on the closed
    quantity at the average cost, and leaves the average cost of the remainder;
  - a fill that crosses through zero closes the old position and opens a new one at
    the fill price.
Commissions reduce realised P&L. Premium is in price points; ``multiplier`` turns a
price-point move into USD.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LedgerFill:
    """One execution, consumed in the order it happened."""

    contract: str       # IB localSymbol — the netting key
    side: str           # "BUY" | "SELL"
    qty: float          # magnitude (> 0)
    price: float        # premium, price points
    commission: float   # USD
    multiplier: float   # price point → USD


@dataclass
class ContractLedger:
    """Folded state for one contract."""

    contract: str
    net_qty: float = 0.0        # signed : + long, − short
    avg_cost: float = 0.0       # avg premium of the OPEN position (0 when flat)
    realized_pnl: float = 0.0   # USD, net of commissions
    commission: float = 0.0     # USD, cumulative
    multiplier: float = 1.0


def _apply(led: ContractLedger, f: LedgerFill) -> None:
    signed = f.qty if f.side == "BUY" else -f.qty
    led.commission += f.commission
    led.realized_pnl -= f.commission
    led.multiplier = f.multiplier
    old = led.net_qty

    # Adding to (or opening) the position → roll the average cost.
    if old == 0 or (old > 0) == (signed > 0):
        new_abs = abs(old) + f.qty
        led.avg_cost = (led.avg_cost * abs(old) + f.price * f.qty) / new_abs
        led.net_qty = old + signed
        return

    # Reducing / closing / flipping → realise P&L on the closed quantity.
    close_qty = min(f.qty, abs(old))
    if old > 0:   # long reduced by a SELL
        led.realized_pnl += (f.price - led.avg_cost) * close_qty * f.multiplier
    else:         # short reduced by a BUY
        led.realized_pnl += (led.avg_cost - f.price) * close_qty * f.multiplier
    led.net_qty = old + signed
    if led.net_qty == 0:
        led.avg_cost = 0.0
    elif (led.net_qty > 0) != (old > 0):
        # crossed through zero → the remainder is a fresh position at this fill price
        led.avg_cost = f.price


def fold_fills(fills: list[LedgerFill]) -> dict[str, ContractLedger]:
    """Fold fills (already in execution order) into a per-contract ledger."""
    out: dict[str, ContractLedger] = {}
    for f in fills:
        led = out.get(f.contract)
        if led is None:
            led = ContractLedger(contract=f.contract, multiplier=f.multiplier)
            out[f.contract] = led
        _apply(led, f)
    return out


def unrealized_pnl(led: ContractLedger, mark: float | None) -> float | None:
    """Mark-to-market P&L of the OPEN position at ``mark`` (premium points).

    Returns 0 when flat, ``None`` when there's an open position but no mark.
    Works for both signs: (mark − cost) × net_qty, so a short (net_qty < 0) gains
    when the mark falls below its cost.
    """
    if led.net_qty == 0:
        return 0.0
    if mark is None:
        return None
    return (mark - led.avg_cost) * led.net_qty * led.multiplier
