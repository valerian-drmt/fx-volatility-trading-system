"""Snapshot-equivalence tests for ``core.pricing.bs``.

The point is not to re-test Black-Scholes from scratch — it is to pin the
exact numerical outputs so a refactor in ``core/`` (vectorisation, caching,
dtype changes) can never silently shift them.

Resurrected from ``tests/old/test_core_bs.py`` (git 14175622~1), extended
with implied-vol round-trip and vanna/volga finite-difference checks.
"""
from __future__ import annotations

import math

import pytest

from core.pricing import bs

pytestmark = pytest.mark.unit

# Fixed reference points. Values computed with the R6 implementation and
# locked in — regenerate only if the pricing formula itself changes.
SCALAR_CASES = [
    # (F, K, T, sigma, right, expected_price)
    (1.08, 1.08, 30 / 365, 0.075, "C", 0.009264050499375953),
    (1.08, 1.09, 60 / 365, 0.075, "P", 0.01876168266401279),
    (1.25, 1.20, 90 / 365, 0.09, "C", 0.055362220302740295),
]


@pytest.mark.parametrize("F,K,T,sigma,right,expected", SCALAR_CASES)
def test_bs_price_snapshot(F, K, T, sigma, right, expected):
    assert bs.bs_price(F, K, T, sigma, right) == pytest.approx(expected, rel=1e-9)


def test_bs_delta_call_atm_is_near_half():
    delta = bs.bs_delta(F=1.08, K=1.08, T=30 / 365, sigma=0.075, right="C")
    assert delta == pytest.approx(0.5043, abs=1e-3)


def test_bs_put_call_parity():
    F, K, T, sigma = 1.10, 1.12, 60 / 365, 0.08
    c = bs.bs_price(F, K, T, sigma, "C")
    p = bs.bs_price(F, K, T, sigma, "P")
    # Forward parity : C - P = F - K (undiscounted).
    assert c - p == pytest.approx(F - K, abs=1e-10)


def test_bs_vega_matches_finite_difference():
    F, K, T, sigma = 1.08, 1.10, 90 / 365, 0.07
    analytical = bs.bs_vega(F, K, T, sigma)
    h = 1e-5
    numerical = (bs.bs_price(F, K, T, sigma + h, "C") - bs.bs_price(F, K, T, sigma - h, "C")) / (2 * h)
    assert analytical == pytest.approx(numerical, rel=1e-4)


def test_degenerate_inputs_return_zero():
    assert bs.bs_price(1.0, 1.0, 0.0, 0.1, "C") == 0.0
    assert bs.bs_price(1.0, 1.0, 30 / 365, 0.0, "C") == 0.0
    assert bs.bs_delta(1.0, 1.0, 30 / 365, -0.1, "C") == 0.0
    assert bs.bs_gamma(0.0, 1.0, 30 / 365, 0.1) == 0.0


def test_interpolate_iv_returns_closest_strike():
    surface = {
        "1M": {
            "strike_atm": 1.08, "sigma_ATM_pct": 7.5,
            "strike_25dc": 1.09, "iv_25dc_pct": 7.8,
            "strike_25dp": 1.07, "iv_25dp_pct": 8.1,
        }
    }
    # closest strike to 1.075 is 1.07 → iv 8.1 % → decimal 0.081
    iv = bs.interpolate_iv(surface, "1M", 1.075, F=1.08)
    assert iv == pytest.approx(0.081, abs=1e-9)


def test_d1_d2_shape_matches_textbook():
    """Spot-check d1 / d2 against the standard formula for one deterministic case."""
    F, K, T, sigma = 1.0, 1.0, 1.0, 0.2
    d1_expected = 0.5 * sigma * math.sqrt(T)
    # Recovered via call delta = N(d1).
    from scipy.stats import norm

    d1 = norm.ppf(bs.bs_delta(F, K, T, sigma, "C"))
    assert d1 == pytest.approx(d1_expected, abs=1e-9)


# ── new coverage (2026-07 remediation) ────────────────────────────────


@pytest.mark.parametrize(
    "F,K,T,sigma,right",
    [
        (1.08, 1.08, 30 / 365, 0.075, "C"),
        (1.08, 1.09, 60 / 365, 0.075, "P"),
        (1.25, 1.20, 90 / 365, 0.09, "C"),
    ],
)
def test_bs_implied_vol_round_trip(F, K, T, sigma, right):
    price = bs.bs_price(F, K, T, sigma, right)
    recovered = bs.bs_implied_vol(price, F, K, T, right)
    assert recovered is not None
    assert recovered == pytest.approx(sigma, abs=1e-5)


def test_bs_implied_vol_returns_none_below_intrinsic():
    # Call intrinsic (undiscounted) is F - K = 0.05 ; a lower price has no
    # BS-consistent sigma.
    assert bs.bs_implied_vol(0.04, F=1.10, K=1.05, T=30 / 365, right="C") is None
    # Degenerate inputs → None.
    assert bs.bs_implied_vol(0.0, F=1.10, K=1.05, T=30 / 365, right="C") is None
    assert bs.bs_implied_vol(0.01, F=1.10, K=1.05, T=0.0, right="C") is None


def test_bs_vanna_matches_finite_difference_of_delta():
    # vanna = ∂Δ/∂σ — check the analytic form against a central difference.
    F, K, T, sigma = 1.08, 1.10, 90 / 365, 0.07
    analytical = bs.bs_vanna(F, K, T, sigma)
    h = 1e-5
    numerical = (
        bs.bs_delta(F, K, T, sigma + h, "C") - bs.bs_delta(F, K, T, sigma - h, "C")
    ) / (2 * h)
    assert analytical == pytest.approx(numerical, rel=1e-4)


def test_bs_volga_matches_finite_difference_of_vega():
    # volga = ∂vega/∂σ — check the analytic form against a central difference.
    F, K, T, sigma = 1.08, 1.10, 90 / 365, 0.07
    analytical = bs.bs_volga(F, K, T, sigma)
    h = 1e-5
    numerical = (bs.bs_vega(F, K, T, sigma + h) - bs.bs_vega(F, K, T, sigma - h)) / (2 * h)
    assert analytical == pytest.approx(numerical, rel=1e-4)
