"""SSVI — Surface Stochastic Volatility Inspired (Gatheral-Jacquier 2014).

Three parameters describe the entire vol surface instead of five per
tenor. The total variance as a function of log-moneyness ``k`` and ATM
total variance ``θ = σ²_ATM · T`` :

    w(k, θ) = (θ / 2) · (1 + ρ φ(θ) k + √((φ(θ) k + ρ)² + (1 - ρ²)))

with ``φ(θ) = η θ^(-γ)``.

Gatheral-Jacquier (2014), Theorems 4.1 and 4.2, for the power-law
``φ(θ) = η θ^(-γ)`` (#311):

* **calendar**: ``θ`` non-decreasing in maturity and ``0 < γ ≤ 1``
  (``∂(θφ)/∂θ = η(1-γ)θ^(-γ) ≥ 0``; the upper bound of the theorem holds
  for every ``γ`` in that range). The old ``2γ ≥ 1-ρ²`` was the Heston-like
  φ's condition, not this one.
* **butterfly**: for every slice ``θ``, ``θφ(θ)(1+|ρ|) < 4`` and
  ``θφ(θ)²(1+|ρ|) ≤ 4``. ``fit_ssvi`` evaluates both on the observed
  slices and reports ``calendar_arb_free`` / ``butterfly_arb_free``; the
  tenor-wise SVI in ``core/vol/svi.py`` checks the density per tenor too.

This module fits ``(η, γ, ρ)`` by minimising squared-error on total
variance across ALL observed (T, K, IV) observations jointly — one
surface fit per vol-engine cycle, stored in the ``ssvi_params`` table.
"""
from __future__ import annotations

from itertools import pairwise
from typing import Any

import numpy as np

_EPS = 1e-10


def _phi(theta: np.ndarray, eta: float, gamma: float) -> np.ndarray:
    theta = np.maximum(theta, _EPS)
    return eta * np.power(theta, -gamma)


def ssvi_total_variance(
    k: np.ndarray, theta: np.ndarray, eta: float, gamma: float, rho: float,
) -> np.ndarray:
    """SSVI total-variance surface ``w(k, θ)`` — vectorised over (k, θ)."""
    phi = _phi(theta, eta, gamma)
    arg = (phi * k + rho)
    return (theta / 2.0) * (1.0 + rho * phi * k + np.sqrt(arg * arg + (1.0 - rho * rho)))


def ssvi_iv(
    k: np.ndarray, tenor_years: float, atm_iv: float,
    eta: float, gamma: float, rho: float,
) -> np.ndarray:
    """Evaluate SSVI implied vol (decimal) at log-moneyness ``k`` for one tenor."""
    theta = np.full_like(np.asarray(k, dtype=float), atm_iv * atm_iv * tenor_years)
    w = ssvi_total_variance(np.asarray(k, dtype=float), theta, eta, gamma, rho)
    return np.sqrt(np.maximum(w, _EPS) / max(tenor_years, _EPS))


def fit_ssvi(
    observations: list[tuple[float, float, float]],
    forward: float,
    atm_iv_by_tenor_years: dict[float, float],
) -> dict[str, float] | None:
    """Joint fit of ``(eta, gamma, rho)`` across all (T, K, iv) observations.

    ``observations`` : list of tuples ``(tenor_years, strike, iv)``.
    ``atm_iv_by_tenor_years`` : map tenor_years → ATM IV (decimal)
    needed to compute ``θ = σ²_ATM · T`` per observation. Must cover
    every tenor that appears in observations.
    """
    from scipy.optimize import least_squares

    if len(observations) < 5 or not atm_iv_by_tenor_years:
        return None
    Ts = np.array([o[0] for o in observations], dtype=float)
    Ks = np.array([o[1] for o in observations], dtype=float)
    ivs = np.array([o[2] for o in observations], dtype=float)
    mask = np.isfinite(Ts) & np.isfinite(Ks) & np.isfinite(ivs) & (Ks > 0) & (ivs > 0)
    Ts, Ks, ivs = Ts[mask], Ks[mask], ivs[mask]
    if len(Ts) < 5:
        return None
    k = np.log(Ks / float(forward))
    w_obs = ivs * ivs * Ts
    theta = np.array(
        [atm_iv_by_tenor_years.get(T, ivs[i])**2 * T for i, T in enumerate(Ts)],
        dtype=float,
    )

    def residuals(x: np.ndarray) -> np.ndarray:
        eta, gamma, rho = x
        w_fit = ssvi_total_variance(k, theta, eta, gamma, rho)
        return w_fit - w_obs

    # Initial guess mid-regime + bounds that keep calendar arb away.
    x0 = np.array([1.0, 0.3, -0.2])
    lo = np.array([1e-3, 0.05, -0.999])
    hi = np.array([10.0, 0.95, 0.999])
    try:
        result = least_squares(residuals, x0, bounds=(lo, hi), method="trf", max_nfev=500)
    except Exception:
        return None
    if not result.success:
        return None
    eta, gamma, rho = (float(v) for v in result.x)
    rmse = float(np.sqrt(np.mean(result.fun**2)))
    slice_thetas = {float(T): float(atm_iv_by_tenor_years.get(T, ivs[i])) ** 2 * float(T) for i, T in enumerate(Ts)}
    calendar_ok, butterfly_ok = ssvi_arbitrage_flags(eta, gamma, rho, slice_thetas)
    # No logging in core (#332): the vol engine logs the flags it gets back.
    return {
        "eta": round(eta, 6),
        "gamma": round(gamma, 6),
        "rho": round(rho, 6),
        "rmse_fit": round(rmse, 6),
        "calendar_arb_free": bool(calendar_ok),
        "butterfly_arb_free": bool(butterfly_ok),
    }


def ssvi_arbitrage_flags(
    eta: float, gamma: float, rho: float, theta_by_tenor_years: dict[float, float],
) -> tuple[bool, bool]:
    """``(calendar_free, butterfly_free)`` for the power-law SSVI (Gatheral-
    Jacquier 2014, Thm 4.1 / 4.2). Calendar: ``θ`` non-decreasing in ``T`` and
    ``0 < γ ≤ 1``. Butterfly, on every observed slice: ``θφ(θ)(1+|ρ|) < 4``
    and ``θφ(θ)²(1+|ρ|) ≤ 4``."""
    tol = 1e-9
    calendar = eta > 0 and 0.0 < gamma <= 1.0 + tol
    ordered = [theta_by_tenor_years[t] for t in sorted(theta_by_tenor_years)]
    if any(b < a - tol for a, b in pairwise(ordered)):
        calendar = False
    butterfly = True
    for theta in theta_by_tenor_years.values():
        phi = float(_phi(np.asarray([theta], dtype=float), eta, gamma)[0])
        if theta * phi * (1.0 + abs(rho)) >= 4.0 - tol or theta * phi * phi * (1.0 + abs(rho)) > 4.0 + tol:
            butterfly = False
            break
    return bool(calendar), bool(butterfly)


def ssvi_curve_for_tenor(
    forward: float,
    tenor_years: float,
    atm_iv: float,
    eta: float,
    gamma: float,
    rho: float,
    k_min: float = -0.08,
    k_max: float = 0.08,
    n_points: int = 40,
) -> list[dict[str, Any]]:
    """Sample an SSVI-derived smile for one tenor on a strike grid."""
    k_grid = np.linspace(k_min, k_max, n_points)
    iv = ssvi_iv(k_grid, tenor_years, atm_iv, eta, gamma, rho)
    strikes = float(forward) * np.exp(k_grid)
    return [
        {"strike": float(s), "iv_pct": float(v) * 100.0}
        for s, v in zip(strikes, iv, strict=True)
    ]
