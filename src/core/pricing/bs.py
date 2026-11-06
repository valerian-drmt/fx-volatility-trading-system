"""
Black-Scholes pricer for EUR FX options (zero rates approximation).
Used for local greeks computation (no IB calls).
"""
from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm


def bs_price(F: float, K: float, T: float, sigma: float, right: str) -> float:
    """BS option price (undiscounted, zero rates)."""
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if right == "C":
        return F * norm.cdf(d1) - K * norm.cdf(d2)
    return K * norm.cdf(-d2) - F * norm.cdf(-d1)


def bs_delta(F: float, K: float, T: float, sigma: float, right: str) -> float:
    """BS delta."""
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    if right == "C":
        return norm.cdf(d1)
    return norm.cdf(d1) - 1.0


def bs_gamma(F: float, K: float, T: float, sigma: float) -> float:
    """BS gamma (same for call and put)."""
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return norm.pdf(d1) / (F * sigma * math.sqrt(T))


def bs_vega(F: float, K: float, T: float, sigma: float) -> float:
    """BS vega (per 1 point of vol, not per 1%)."""
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return F * norm.pdf(d1) * math.sqrt(T)


def bs_theta(F: float, K: float, T: float, sigma: float, right: str) -> float:
    """BS theta (per day, undiscounted)."""
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    # Theta per year, divide by 365 for per day
    theta_year = -(F * norm.pdf(d1) * sigma) / (2.0 * math.sqrt(T))
    return theta_year / 365.0


def bs_vanna(F: float, K: float, T: float, sigma: float) -> float:
    """∂²P/∂S∂σ — vanna in raw unit terms (same for call and put).

    Multiply by ``qty × multiplier × 0.01`` to express in ``$/volpt``
    (Δ change in $ per 1 vol pt move of IV).
    """
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return -norm.pdf(d1) * d2 / sigma


def bs_volga(F: float, K: float, T: float, sigma: float) -> float:
    """∂²P/∂σ² — volga in raw unit terms (same for call and put).

    Multiply by ``qty × multiplier × (0.01) ** 2`` to express in ``$/volpt²``
    (vega change in $ per 1 vol pt² of IV move).
    """
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    vega_unit = F * norm.pdf(d1) * sqrt_t
    return vega_unit * d1 * d2 / sigma


def bs_implied_vol(
    price: float, F: float, K: float, T: float, right: str,
    lo: float = 1e-4, hi: float = 5.0,
) -> float | None:
    """Solve sigma such that ``bs_price(F, K, T, sigma, right) = price``.

    Returns None if the price is outside the no-arbitrage envelope for any
    sigma in [lo, hi] — typical with stale market prices on deep OTM options
    or when the contract has 0 days to expiry.
    """
    if price <= 0 or F <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(F - K, 0.0) if right == "C" else max(K - F, 0.0)
    if price < intrinsic - 1e-8:
        return None  # arbitrage / stale price

    def _f(sigma: float) -> float:
        return bs_price(F, K, T, sigma, right) - price

    try:
        if _f(lo) * _f(hi) > 0:
            return None  # price outside [lo, hi] reachable range
        return float(brentq(_f, lo, hi, xtol=1e-6, maxiter=64))
    except (ValueError, RuntimeError):
        return None


def interpolate_iv(
    iv_surface: dict, tenor: str, strike: float, F: float, T: float | None = None,
) -> float | None:
    """IV (decimal) at ``strike`` on the published surface
    ``{tenor: {label: {"iv": σ, "strike": K}}}`` — linear in strike between
    the bracketing pillars, flat beyond the wings.

    Thin wrapper over :func:`core.vol.iv_lookup.iv_at_strike`, kept for the
    existing call sites. The previous body read the retired flat schema
    (``sigma_ATM_pct`` / ``strike_atm``) and returned None on every live
    surface, which silently disabled live re-pricing and the delta hedge
    (audit QM-02 / R3-02, #310).
    """
    from core.vol.iv_lookup import iv_at_strike

    res = iv_at_strike(iv_surface, tenor, strike, F=F, T=T)
    return res.iv if res is not None else None
