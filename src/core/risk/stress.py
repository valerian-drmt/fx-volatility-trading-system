"""Pure stress / scenario revaluation (R11 G-risk 5.2-5.3).

Generalises the spot×IV stress-grid + spot greeks-ladder to a parameterised
engine over four shock axes — spot / vol / time / skew / fly — and seven outputs
(pnl + the six greeks). Kept in ``core`` (pure Black-Scholes math, no I/O) ; the
router resolves ``OpenPosition`` rows into the plain ``ResolvedPosition`` dicts
this module consumes.

Shock conventions (stylised but documented — a desk stress tool, not a smile
recalibration):
  - spot  : ``F → F·(1 + dspot_frac)``
  - vol   : parallel ``σ → σ + dvol_vp/100`` (all options)
  - time  : ``T → max(ε, T − dt_days/365)``
  - skew  : 25Δ-RR proxy. ``σ += (dskew_vp/100)·0.5·w`` with moneyness weight
            ``w = clip((K−F)/(F·MONEY_BAND), −1, +1)`` → +ΔRR enriches call wing,
            cheapens put wing, leaves ATM flat.
  - fly   : 25Δ-BF proxy. ``σ += (dfly_vp/100)·|w|`` → wings up vs ATM.
"""
from __future__ import annotations

from typing import Any, Literal

from core.pricing.bs import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_theta,
    bs_vanna,
    bs_vega,
    bs_volga,
)

Output = Literal["pnl", "delta", "gamma", "vega", "theta", "vanna", "volga"]
Axis = Literal["spot-vol", "spot-time", "spot-skew", "spot-fly"]

# Moneyness band over which the skew/fly weight saturates to ±1 (±5% ≈ deep wings).
MONEY_BAND = 0.05
# Greek display scalings — match the existing greeks-ladder ($/pip, $/volpt).
_GAMMA_PER_PIP = 1e-4
_VEGA_PER_VOLPT = 0.01
_VANNA_SCALE = 1e-4   # per pip · per vol pt
_VOLGA_SCALE = 1e-4   # per vol pt²

ResolvedPosition = dict[str, Any]


def _money_weight(strike: float, spot: float) -> float:
    if spot <= 0:
        return 0.0
    w = (strike - spot) / (spot * MONEY_BAND)
    return max(-1.0, min(1.0, w))


def _shocked_iv(p: ResolvedPosition, spot: float, dvol_vp: float, dskew_vp: float, dfly_vp: float) -> float:
    """Per-option shocked IV given the parallel/skew/fly shocks (vol points)."""
    iv = p["iv"] + dvol_vp / 100.0
    if dskew_vp or dfly_vp:
        w = _money_weight(p["K"], spot)
        iv += (dskew_vp / 100.0) * 0.5 * w        # RR : signed by moneyness
        iv += (dfly_vp / 100.0) * abs(w)          # BF : wings up vs ATM
    return iv


def _greek(output: Output, qm: float, spot: float, p: ResolvedPosition, iv: float) -> float:
    """One option's contribution to the requested greek output (display-scaled)."""
    K, T, right = p["K"], p["T"], p["right"]
    if output == "delta":
        return qm * bs_delta(spot, K, T, iv, right)
    if output == "gamma":
        return qm * bs_gamma(spot, K, T, iv) * _GAMMA_PER_PIP
    if output == "vega":
        return qm * bs_vega(spot, K, T, iv) * _VEGA_PER_VOLPT
    if output == "theta":
        return qm * bs_theta(spot, K, T, iv, right)
    if output == "vanna":
        return qm * bs_vanna(spot, K, T, iv) * _VANNA_SCALE
    if output == "volga":
        return qm * bs_volga(spot, K, T, iv) * _VOLGA_SCALE
    return 0.0


def reval_book(
    baselines: list[ResolvedPosition],
    spot0: float,
    *,
    dspot_bp: float = 0.0,
    dvol_vp: float = 0.0,
    dt_days: float = 0.0,
    dskew_vp: float = 0.0,
    dfly_vp: float = 0.0,
    output: Output = "pnl",
) -> float:
    """Revalue the book under one scenario and return the summed ``output``.

    ``output="pnl"`` → Σ (NPV_scenario − NPV_base). Any greek → Σ that greek
    evaluated at the shocked scenario (the book's stock of that greek there).
    Futures contribute delta = qty·mult and pnl = qty·mult·ΔF only.
    """
    new_spot = spot0 * (1.0 + dspot_bp / 10000.0)
    total = 0.0
    for p in baselines:
        qm = p["qty_signed"] * p["mult"]
        if p["type"] == "FUTURE":
            if output == "pnl":
                total += qm * (new_spot - spot0)
            elif output == "delta":
                total += qm
            continue
        new_T = max(1e-4, p["T"] - dt_days / 365.0)
        new_iv = _shocked_iv(p, new_spot, dvol_vp, dskew_vp, dfly_vp)
        if new_iv <= 0:
            continue
        shocked = {**p, "T": new_T}
        if output == "pnl":
            total += qm * (bs_price(new_spot, p["K"], new_T, new_iv, p["right"]) - p["price_base"])
        else:
            total += _greek(output, qm, new_spot, shocked, new_iv)
    return total
