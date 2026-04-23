"""Raw SVI (Gatheral) fit on a single-tenor smile.

The raw SVI parametrisation expresses the total implied variance
``w(k) = sigma(k)^2 * T`` as a function of log-moneyness
``k = ln(K / F)`` :

    w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma_p^2))

Five free parameters :

- ``a``     : vertical level (min total variance asymptote)
- ``b``     : overall tightness / wing slope magnitude
- ``rho``   : correlation / skew, in [-1, 1]
- ``m``     : horizontal shift (ATM offset in log-moneyness)
- ``sigma`` : convexity at m (ATM curvature)

Industry-standard for single-tenor vol smile interpolation. Reference :
Gatheral, "A parsimonious arbitrage-free implied volatility
parameterization", Global Derivatives 2004.

This implementation keeps scope minimal :
- five data points per tenor fit cleanly in practice (we scan 5 pillars
  10P/25P/ATM/25C/10P after ``interpolate_delta_pillars``)
- no butterfly / calendar arbitrage constraints — bounded-least-squares
  with sane parameter bounds is enough to avoid degenerate fits
- single tenor (no SSVI surface fit) — each smile is fit independently
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

_EPS = 1e-10


@dataclass(frozen=True)
class SviParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float


def _total_variance(k: np.ndarray, params: SviParams) -> np.ndarray:
    diff = k - params.m
    return params.a + params.b * (
        params.rho * diff + np.sqrt(diff * diff + params.sigma * params.sigma)
    )


def svi_iv(k: np.ndarray, params: SviParams, tenor_years: float) -> np.ndarray:
    """Implied volatility (decimal, e.g. 0.06) from SVI params at log-moneyness ``k``."""
    w = np.maximum(_total_variance(k, params), _EPS)
    return np.sqrt(w / max(tenor_years, _EPS))


def fit_svi(
    strikes: np.ndarray | list[float],
    ivs: np.ndarray | list[float],
    forward: float,
    tenor_years: float,
) -> SviParams | None:
    """Least-squares fit of SVI params to (strike, iv) observations.

    Returns ``None`` when the fit is degenerate (fewer than 3 points, or
    the optimiser fails to converge). Caller falls back to the raw
    observed smile in that case.
    """
    from scipy.optimize import least_squares

    K = np.asarray(strikes, dtype=float)
    iv = np.asarray(ivs, dtype=float)
    mask = np.isfinite(K) & np.isfinite(iv) & (K > 0) & (iv > 0)
    K, iv = K[mask], iv[mask]
    if len(K) < 3:
        return None

    T = max(float(tenor_years), _EPS)
    k = np.log(K / float(forward))
    w_obs = iv * iv * T  # observed total variance

    # Initial guess : a ~ min variance, b small, rho ~ -0.2 (put skew), m ~ 0, sigma ~ 0.1.
    x0 = np.array([float(np.min(w_obs)) * 0.9, 0.04, -0.2, 0.0, 0.1])
    # Bounds keep the fit in the arbitrage-plausible region without full
    # butterfly constraints : b >= 0, |rho| < 1, sigma > 0.
    lo = np.array([0.0, 0.0, -0.999, -1.0, 1e-3])
    hi = np.array([float(np.max(w_obs)) * 2.0 + 1e-3, 1.0, 0.999, 1.0, 2.0])

    def residuals(x: np.ndarray) -> np.ndarray:
        p = SviParams(a=x[0], b=x[1], rho=x[2], m=x[3], sigma=x[4])
        return _total_variance(k, p) - w_obs

    try:
        result = least_squares(residuals, x0, bounds=(lo, hi), method="trf", max_nfev=400)
    except Exception:
        return None
    if not result.success:
        return None
    return SviParams(a=result.x[0], b=result.x[1], rho=result.x[2], m=result.x[3], sigma=result.x[4])


def butterfly_g_min(
    params: SviParams,
    k_min: float = -0.20,
    k_max: float = 0.20,
    n_grid: int = 400,
) -> float:
    """Butterfly-arbitrage density check : min of g(k) on the grid.

    Gatheral's butterfly-free condition requires the "density function" :

        g(k) = (1 - k w'(k) / (2 w(k)))^2
             - (w'(k))^2 / 4 * (1 / w(k) + 1 / 4)
             + w''(k) / 2

    to be ≥ 0 for every log-moneyness ``k``. Negative g(k) means the
    fitted smile implies a negative risk-neutral density around that
    strike — bad news for any pricing based on this fit. The helper
    returns the **min of g over the grid** ; callers log a WARNING
    when this is < 0 and optionally drop the tenor from trade preview.
    """
    k = np.linspace(k_min, k_max, n_grid)
    diff = k - params.m
    sq = np.sqrt(diff * diff + params.sigma * params.sigma)
    w = params.a + params.b * (params.rho * diff + sq)
    w = np.maximum(w, _EPS)
    # First and second derivatives (analytic).
    w_p = params.b * (params.rho + diff / sq)
    w_pp = params.b * (params.sigma * params.sigma) / (sq**3)
    term1 = (1.0 - k * w_p / (2.0 * w)) ** 2
    term2 = (w_p**2) / 4.0 * (1.0 / w + 0.25)
    term3 = w_pp / 2.0
    g = term1 - term2 + term3
    return float(np.min(g))


def svi_curve(
    forward: float,
    tenor_years: float,
    params: SviParams,
    k_min: float = -0.08,
    k_max: float = 0.08,
    n_points: int = 40,
) -> list[dict[str, Any]]:
    """Sample the fitted SVI at ``n_points`` log-moneyness grid points.

    Returns a list of ``{strike, iv_pct}`` suitable for JSON serialisation
    (iv expressed as a percent, 6.05 rather than 0.0605 — matches the
    SmilePoint contract already used by the API).
    """
    k_grid = np.linspace(k_min, k_max, n_points)
    iv = svi_iv(k_grid, params, tenor_years)
    strikes = float(forward) * np.exp(k_grid)
    return [
        {"strike": float(s), "iv_pct": float(v) * 100.0}
        for s, v in zip(strikes, iv, strict=True)
    ]
