"""
Black-Scholes pricer for EUR FX options (zero rates approximation).
Used for local greeks computation (no IB calls).
"""
from __future__ import annotations

import math

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


def interpolate_iv(iv_surface: dict, tenor: str, strike: float, F: float) -> float | None:
    """Get IV for a position by interpolating from the vol scanner surface.

    iv_surface: {tenor: {sigma_ATM_pct, strike_atm, iv_25dp_pct, strike_25dp, ...}}
    Returns IV as decimal (not %).
    """
    pillar = iv_surface.get(tenor)
    if pillar is None:
        return None

    # Build (strike, iv) pairs from the pillar
    points = []
    for label in ["10dp", "25dp", "atm", "25dc", "10dc"]:
        if label == "atm":
            k_key, iv_key = "strike_atm", "sigma_ATM_pct"
        else:
            k_key, iv_key = f"strike_{label}", f"iv_{label}_pct"
        k = pillar.get(k_key)
        iv = pillar.get(iv_key)
        if k is not None and iv is not None:
            points.append((k, iv / 100.0))  # convert % to decimal

    if not points:
        return None

    # Find closest strike
    points.sort(key=lambda p: p[0])
    closest = min(points, key=lambda p: abs(p[0] - strike))
    return closest[1]
