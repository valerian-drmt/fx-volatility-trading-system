"""Mark-to-market and P&L attribution for an open position.

Pure functions ; the orchestrator passes plain numbers (entry snapshot,
current spot, current IV) and gets back the dataclasses.

Attribution model (linearised, vs entry snapshot) :
    pnl_gross  = mark_now - entry_premium
    vega_pnl   = vega_entry      × (iv_now_vol_pts - iv_entry_vol_pts)
    gamma_pnl  = ½ × gamma_entry × (spot_now - spot_entry)²        (in pip² units)
    theta_pnl  = theta_entry     × days_elapsed                     (negative for long)
    other_pnl  = pnl_gross - (vega + gamma + theta)                 (residual jumps + non-linearity)

For monitoring purposes this linearisation is good enough. Full re-pricing
is reserved for closing P&L.

Units : every ``*_usd`` input/output follows the USD-at-notional
convention of ``core.units`` (premium/mark in real USD, vega $/vol-pt,
gamma $/pip² with shocks in pips, theta $/day). The formulas here are
scale-agnostic — they are only correct when the inputs respect it.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.units import PIP_SIZE  # EUR/USD pip — single source of truth


@dataclass(frozen=True)
class MtmResult:
    pnl_gross_usd: float           # mark - entry premium (sign-aware)
    pnl_net_usd: float             # gross - entry_cost - hedge_cost_cumul
    mark_value_usd: float          # current MTM value of the position
    spot_now: float
    iv_now_pct: float


@dataclass(frozen=True)
class PnlAttribution:
    vega_usd: float
    gamma_usd: float
    theta_usd: float
    other_usd: float

    @property
    def total_attributed(self) -> float:
        return self.vega_usd + self.gamma_usd + self.theta_usd + self.other_usd


def compute_mtm(
    *,
    entry_premium_usd: float,
    mark_value_usd: float,
    entry_total_cost_usd: float,
    hedge_cost_cumul_usd: float,
    spot_now: float,
    iv_now_pct: float,
) -> MtmResult:
    """Compute mark-to-market P&L of a position.

    Conventions :
        - entry_premium_usd : net premium paid (>0 long net, <0 short net)
        - mark_value_usd    : current value of the structure (analogous sign to premium)
        - costs are positive and subtracted from gross
    """
    pnl_gross = mark_value_usd - entry_premium_usd
    pnl_net = pnl_gross - entry_total_cost_usd - hedge_cost_cumul_usd
    return MtmResult(
        pnl_gross_usd=pnl_gross,
        pnl_net_usd=pnl_net,
        mark_value_usd=mark_value_usd,
        spot_now=spot_now,
        iv_now_pct=iv_now_pct,
    )


def attribute_pnl(
    *,
    pnl_gross_usd: float,
    entry_vega_usd_per_volpt: float,
    entry_gamma_usd_per_pip2: float,
    entry_theta_usd_per_day: float,
    iv_entry_pct: float,
    iv_now_pct: float,
    spot_entry: float,
    spot_now: float,
    days_elapsed: float,
) -> PnlAttribution:
    """Linearised P&L attribution against entry-time greeks.

    Vol-points are in 0.01 IV units (so iv_now_pct=7.5 means 7.5 vol-points
    above 0). Gamma is in $/pip² of spot move.
    """
    # Vega : Δ(iv) in vol points × vega
    delta_iv_volpts = iv_now_pct - iv_entry_pct
    vega = entry_vega_usd_per_volpt * delta_iv_volpts

    # Gamma : ½ × γ × (Δspot in pips)²
    delta_spot_pips = (spot_now - spot_entry) / PIP_SIZE
    gamma = 0.5 * entry_gamma_usd_per_pip2 * delta_spot_pips * delta_spot_pips

    # Theta : per-day decay × days elapsed
    theta = entry_theta_usd_per_day * days_elapsed

    other = pnl_gross_usd - (vega + gamma + theta)
    return PnlAttribution(vega_usd=vega, gamma_usd=gamma, theta_usd=theta, other_usd=other)
