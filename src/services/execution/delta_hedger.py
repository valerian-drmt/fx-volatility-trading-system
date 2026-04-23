"""Delta hedger — static / threshold / scheduled (Phase P5.2).

Decides whether to fire an EUR FUT hedge order based on the current
net delta of the open vol structures and the configured mode. The
decision is a pure function ; the actual IB call is routed through the
order router (not implemented in sandbox).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["static", "threshold", "scheduled"]


@dataclass(frozen=True)
class HedgeDecision:
    should_hedge: bool
    qty: int                # negative = SELL, positive = BUY. 0 = no action
    reason: str


def decide_hedge(
    net_delta: float,
    mode: Mode = "threshold",
    threshold: float = 0.05,
    last_hedge_seconds_ago: float | None = None,
    rebalance_every_s: float = 900.0,
) -> HedgeDecision:
    """Return whether to fire a hedge now, and for what quantity.

    - ``static`` : never rebalances after entry (caller hedges once on
      entry and lets the structure drift).
    - ``threshold`` : hedge if |delta| exceeds ``threshold``.
    - ``scheduled`` : hedge every ``rebalance_every_s`` regardless of
      the current delta, rounding it to the nearest integer FUT.
    """
    if mode == "static":
        return HedgeDecision(False, 0, "static: no rebalance after entry")
    if mode == "threshold":
        if abs(net_delta) > threshold:
            qty = -round(net_delta)
            return HedgeDecision(True, qty, f"threshold: |delta|={abs(net_delta):.3f} > {threshold}")
        return HedgeDecision(False, 0, f"threshold: |delta|={abs(net_delta):.3f} ≤ {threshold}")
    if mode == "scheduled":
        if last_hedge_seconds_ago is None or last_hedge_seconds_ago >= rebalance_every_s:
            qty = -round(net_delta)
            return HedgeDecision(qty != 0, qty, f"scheduled: {rebalance_every_s}s elapsed")
        return HedgeDecision(False, 0, f"scheduled: {last_hedge_seconds_ago:.0f}s/{rebalance_every_s}s")
    return HedgeDecision(False, 0, f"unknown mode {mode!r}")


@dataclass(frozen=True)
class HedgePnl:
    options_pnl: float
    hedge_pnl: float
    total: float


def split_hedge_pnl(
    option_mtm_change: float,
    hedge_qty: int,
    spot_change: float,
    fut_multiplier: float = 125000.0,
) -> HedgePnl:
    """Decompose period P&L into option leg vs hedge leg contributions."""
    hedge = hedge_qty * spot_change * fut_multiplier
    return HedgePnl(
        options_pnl=float(option_mtm_change),
        hedge_pnl=float(hedge),
        total=float(option_mtm_change + hedge),
    )
