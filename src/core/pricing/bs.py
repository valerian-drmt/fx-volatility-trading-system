"""
Black-Scholes (Black-76 on the forward) for EUR FX options. THE pricing
kernel: ``core.trade_preview`` and ``core.structures`` price through these
functions, the vectorised ``core.risk.greeks`` mirrors them (#331).

Conventions (``docs/vol-modeling/pricing.md``):

* undiscounted, zero rates — the forward is the CME future; premium-paid
  FOPs carry a known bias of about ``r_USD * T`` that is documented, not
  modelled;
* ``T`` in years, ``sigma`` as a fraction (0.07), ``right`` in {"C", "P"};
* every function returns a plain ``float`` (never a numpy scalar: the preview
  stores comparisons on these in JSON columns);
* at the boundary (``T <= 0`` or ``sigma <= 0``) the price is the intrinsic
  value and the greeks their limits (delta = the step, gamma / vega / theta /
  vanna / volga = 0): an option on expiry day is worth its intrinsic, not
  zero (audit QM-01); ``F <= 0`` / ``K <= 0`` is not an option at all and
  prices 0 with zero greeks.
"""
from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm


def _intrinsic(F: float, K: float, right: str) -> float:
    return max(F - K, 0.0) if right == "C" else max(K - F, 0.0)


def _degenerate(F: float, K: float, T: float, sigma: float) -> bool:
    """True when there is no diffusion left to price: expiry or zero vol."""
    return T <= 0 or sigma <= 0


def _invalid(F: float, K: float) -> bool:
    return F <= 0 or K <= 0 or not (math.isfinite(F) and math.isfinite(K))


def _d1(F: float, K: float, T: float, sigma: float) -> float:
    return (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))


def bs_price(F: float, K: float, T: float, sigma: float, right: str) -> float:
    """Option price (undiscounted). Intrinsic at expiry / zero vol."""
    if _invalid(F, K):
        return 0.0
    if _degenerate(F, K, T, sigma):
        return _intrinsic(F, K, right)
    d1 = _d1(F, K, T, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    if right == "C":
        return float(F * norm.cdf(d1) - K * norm.cdf(d2))
    return float(K * norm.cdf(-d2) - F * norm.cdf(-d1))


def bs_delta(F: float, K: float, T: float, sigma: float, right: str) -> float:
    """Delta. At the boundary the step: call 1 if F > K else 0, put -1 if F < K else 0."""
    if _invalid(F, K):
        return 0.0
    if _degenerate(F, K, T, sigma):
        if right == "C":
            return 1.0 if F > K else 0.0
        return -1.0 if F < K else 0.0
    d1 = _d1(F, K, T, sigma)
    if right == "C":
        return float(norm.cdf(d1))
    return float(norm.cdf(d1) - 1.0)


def bs_gamma(F: float, K: float, T: float, sigma: float) -> float:
    """Gamma (same for call and put); 0 at the boundary."""
    if _invalid(F, K) or _degenerate(F, K, T, sigma):
        return 0.0
    d1 = _d1(F, K, T, sigma)
    return float(norm.pdf(d1) / (F * sigma * math.sqrt(T)))


def bs_vega(F: float, K: float, T: float, sigma: float) -> float:
    """Vega per unit of sigma (per 1.00, not per 1 %); 0 at the boundary."""
    if _invalid(F, K) or _degenerate(F, K, T, sigma):
        return 0.0
    d1 = _d1(F, K, T, sigma)
    return float(F * norm.pdf(d1) * math.sqrt(T))


def bs_theta(F: float, K: float, T: float, sigma: float, right: str) -> float:
    """Theta per DAY (undiscounted, so call = put); 0 at the boundary."""
    if _invalid(F, K) or _degenerate(F, K, T, sigma):
        return 0.0
    d1 = _d1(F, K, T, sigma)
    theta_year = -(F * norm.pdf(d1) * sigma) / (2.0 * math.sqrt(T))
    return float(theta_year / 365.0)


def bs_vanna(F: float, K: float, T: float, sigma: float) -> float:
    """∂²P/∂S∂σ in raw units (same for call and put); 0 at the boundary.

    Multiply by ``qty × multiplier × 0.01`` to express in ``$/volpt``.
    """
    if _invalid(F, K) or _degenerate(F, K, T, sigma):
        return 0.0
    sqrt_t = math.sqrt(T)
    d1 = _d1(F, K, T, sigma)
    d2 = d1 - sigma * sqrt_t
    return float(-norm.pdf(d1) * d2 / sigma)


def bs_volga(F: float, K: float, T: float, sigma: float) -> float:
    """∂²P/∂σ² in raw units (same for call and put); 0 at the boundary.

    Multiply by ``qty × multiplier × (0.01) ** 2`` to express in ``$/volpt²``.
    """
    if _invalid(F, K) or _degenerate(F, K, T, sigma):
        return 0.0
    sqrt_t = math.sqrt(T)
    d1 = _d1(F, K, T, sigma)
    d2 = d1 - sigma * sqrt_t
    vega_unit = F * norm.pdf(d1) * sqrt_t
    return float(vega_unit * d1 * d2 / sigma)


def bs_greeks(F: float, K: float, T: float, sigma: float, right: str) -> dict[str, float]:
    """delta / gamma / vega / theta in RAW units from the same kernel:
    delta a fraction, gamma per unit of F², vega per unit of sigma, theta per
    YEAR. Callers scale (``/100`` for vol points, ``/365`` for per day)."""
    return {
        "delta": bs_delta(F, K, T, sigma, right),
        "gamma": bs_gamma(F, K, T, sigma),
        "vega": bs_vega(F, K, T, sigma),
        "theta": bs_theta(F, K, T, sigma, right) * 365.0,
    }


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
