"""Vectorised BS helpers for portfolio PnL curves.

The scalar closed-form lives in :mod:`core.pricing.bs` — this module mirrors
the same maths but over a ``numpy`` array of forwards, which is the hot path
inside the risk-engine PnL chart (N≈200 spots × M positions).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm


def bs_price_vec(
    F_arr: np.ndarray,
    K: float,
    T: float,
    sigma: float,
    right: str,
) -> np.ndarray:
    """Undiscounted BS price over an array of forwards.

    Returns a zero vector when any of ``sigma``, ``T``, ``K`` is non-positive
    (degenerate option) — matching the scalar convention so the caller does
    not need to special-case it.
    """
    if sigma <= 0 or T <= 0 or K <= 0:
        return np.zeros_like(F_arr)
    sqrt_T = math.sqrt(T)
    log_FK = np.log(F_arr / K)
    d1 = (log_FK + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    if right == "C":
        return F_arr * norm.cdf(d1) - K * norm.cdf(d2)
    return K * norm.cdf(-d2) - F_arr * norm.cdf(-d1)
