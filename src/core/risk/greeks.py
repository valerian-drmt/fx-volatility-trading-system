"""Vectorised BS helpers for portfolio PnL curves.

The scalar closed-form lives in :mod:`core.pricing.bs` — this module mirrors
the same maths over a ``numpy`` array of forwards, which is the hot path
inside the risk PnL chart (N≈200 spots × M positions). Same conventions as
the kernel (#309 / #331): intrinsic value at ``T <= 0`` / ``sigma <= 0``,
and an element whose forward is non-positive or not finite prices 0 instead
of propagating a NaN through the ladder (audit QM-14).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm


def _intrinsic_vec(F: np.ndarray, K: float, right: str) -> np.ndarray:
    return np.maximum(F - K, 0.0) if right == "C" else np.maximum(K - F, 0.0)


def bs_price_vec(
    F_arr: np.ndarray,
    K: float,
    T: float,
    sigma: float,
    right: str,
) -> np.ndarray:
    """Undiscounted BS price over an array of forwards, elementwise equal to
    :func:`core.pricing.bs.bs_price` at each F.

    * ``K <= 0``: not an option, a zero vector.
    * ``T <= 0`` or ``sigma <= 0``: the intrinsic value vector.
    * an element with ``F <= 0`` or a non-finite F: 0 at that element, the
      others priced normally (the scalar kernel's ``F <= 0 -> 0`` rule).
    """
    F = np.asarray(F_arr, dtype=float)
    out = np.zeros_like(F)
    if K <= 0 or not math.isfinite(K):
        return out
    valid = np.isfinite(F) & (F > 0)
    if not valid.any():
        return out
    Fv = F[valid]
    if sigma <= 0 or T <= 0:
        out[valid] = _intrinsic_vec(Fv, K, right)
        return out
    sqrt_T = math.sqrt(T)
    d1 = (np.log(Fv / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    if right == "C":
        out[valid] = Fv * norm.cdf(d1) - K * norm.cdf(d2)
    else:
        out[valid] = K * norm.cdf(-d2) - Fv * norm.cdf(-d1)
    return out
