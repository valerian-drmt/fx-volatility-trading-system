"""Live position re-pricing — replaces the linearised attribution of
``compute_mtm`` for monitoring (cf. STEP5 §9.2).

Pure helpers : the orchestrator passes the leg list + a surface dict + spot,
gets back per-leg + position-level mark + greeks. No DB / Redis coupling.

A leg is described by the minimal set of fields persisted on
``structure_orders`` :
    contract_type ('call' | 'put'), strike (in spot units), expiry (date),
    side ('BUY' | 'SELL'), qty (int), tenor (str — used to pick the surface
    pillar), contract_symbol (default 'EUR').

When the surface lookup fails for a leg, we fall back to the IV that was
recorded on entry (``preview_iv_pct``) — the caller decides whether to
treat that as a partial-confidence mark.

Greeks aggregation
------------------
USD-at-notional convention — see ``core.units`` (the single source of
truth). Mark, vega ($/vol-pt), gamma ($/pip²) and theta ($/day) are all
scaled by ``contract_multiplier`` (default : CME EUR FOP €125 000) so they
agree with the preview-side ``core.trade_preview`` numbers. ``total_delta``
stays a *contract-equivalent* delta (Σ bs_delta × qty_signed, NO
notional) — that is what the delta hedger consumes (hedge qty in
contracts). For a position ``side ∈ {BUY, SELL}`` and qty ``q``, we
multiply by ``+q`` (BUY) or ``-q`` (SELL) and sum across legs.

Per-leg ``LegPricing`` values stay raw/unscaled (price points, per unit
of notional) — they are diagnostic ; only the position-level totals carry
the USD convention.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from core.pricing.bs import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_theta,
    bs_vega,
    interpolate_iv,
)
from core.units import EUR_FOP_MULTIPLIER, PIP_SIZE, VOLPT


@dataclass(frozen=True)
class LegSpec:
    leg_idx: int
    contract_type: str    # 'call' | 'put'
    strike: float
    expiry: date
    tenor: str            # '1M' | '3M' | …
    side: str             # 'BUY' | 'SELL'
    qty: int
    fallback_iv: float | None = None   # preview_iv_pct ÷ 100, used if surface miss


@dataclass(frozen=True)
class LegPricing:
    leg_idx: int
    price: float          # BS price, undiscounted
    delta: float          # contract delta (per 1 unit notional)
    gamma: float          # per pip²
    vega: float           # per vol-point
    theta: float          # per day
    iv_used: float        # decimal IV applied
    sign: int             # +1 BUY, -1 SELL
    qty_signed: int       # sign * qty


@dataclass(frozen=True)
class PositionMark:
    mark_value_usd: float         # real USD at notional (core.units convention)
    total_delta: float            # contract-equivalent (positive long EUR exposure), NO notional
    total_gamma_usd_per_pip2: float
    total_vega_usd_per_volpt: float
    total_theta_usd_per_day: float
    legs: list[LegPricing]
    n_surface_missing: int        # legs that fell back to entry IV


def _years_to_expiry(expiry: date, now: datetime) -> float:
    """Year fraction between now (UTC date) and expiry. Floors at ~0."""
    days = max(0, (expiry - now.date()).days)
    return days / 365.0


def price_position(
    *,
    legs: Sequence[LegSpec],
    surface: dict[str, Any] | None,
    spot: float,
    now: datetime,
    contract_multiplier: float = EUR_FOP_MULTIPLIER,
) -> PositionMark:
    """Re-price every leg with BS + surface IV. Sums into a position-level mark.

    ``contract_multiplier`` scales mark/gamma/vega/theta into real USD at
    notional (core.units). Default = CME EUR FOP full size (€125 000) ;
    micro (M6E) callers pass 12 500. ``total_delta`` is NOT scaled (see
    module docstring).
    """
    mark = 0.0
    total_delta = 0.0
    total_gamma = 0.0
    total_vega = 0.0
    total_theta = 0.0
    leg_results: list[LegPricing] = []
    n_missing = 0

    for leg in legs:
        T = _years_to_expiry(leg.expiry, now)
        right = "C" if leg.contract_type.lower() in ("call", "c") else "P"
        sigma: float | None = None
        if surface is not None:
            sigma = interpolate_iv(surface, leg.tenor, leg.strike, spot)
        if sigma is None:
            sigma = leg.fallback_iv
            if sigma is not None:
                n_missing += 1
        if sigma is None or sigma <= 0:
            # Cannot price ; emit a zero-greek leg so the caller sees the gap.
            leg_results.append(LegPricing(
                leg_idx=leg.leg_idx, price=0.0, delta=0.0, gamma=0.0, vega=0.0,
                theta=0.0, iv_used=0.0, sign=0, qty_signed=0,
            ))
            n_missing += 1
            continue

        price = bs_price(spot, leg.strike, T, sigma, right)
        delta = bs_delta(spot, leg.strike, T, sigma, right)
        gamma = bs_gamma(spot, leg.strike, T, sigma)
        vega = bs_vega(spot, leg.strike, T, sigma)
        theta = bs_theta(spot, leg.strike, T, sigma, right)

        sign = +1 if leg.side.upper() == "BUY" else -1
        qty_signed = sign * int(leg.qty)

        # Mark : long premium positive ; signed price × |qty| × notional
        # (bs_price is in price points — × contract_multiplier = real USD).
        mark += sign * price * abs(qty_signed) * contract_multiplier

        # Greeks (core.units) : delta stays contract-equivalent ; γ in
        # $/pip² (bs_gamma × pip² × notional) ; vega in $/volpt (bs_vega ×
        # 0.01 × notional) ; theta already per-day from core.pricing.bs —
        # scaled by notional only, no second /365.
        total_delta += delta * qty_signed
        total_gamma += gamma * (PIP_SIZE * PIP_SIZE) * qty_signed * contract_multiplier
        total_vega += vega * VOLPT * qty_signed * contract_multiplier
        total_theta += theta * qty_signed * contract_multiplier

        leg_results.append(LegPricing(
            leg_idx=leg.leg_idx, price=price, delta=delta, gamma=gamma,
            vega=vega, theta=theta, iv_used=sigma, sign=sign,
            qty_signed=qty_signed,
        ))

    return PositionMark(
        mark_value_usd=mark,
        total_delta=total_delta,
        total_gamma_usd_per_pip2=total_gamma,
        total_vega_usd_per_volpt=total_vega,
        total_theta_usd_per_day=total_theta,
        legs=leg_results,
        n_surface_missing=n_missing,
    )
