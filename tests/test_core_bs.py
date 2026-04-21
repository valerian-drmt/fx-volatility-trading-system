"""Snapshot-equivalence tests for ``core.pricing.bs``.

The point is not to re-test Black-Scholes from scratch — it is to pin the
exact numerical outputs so a refactor in ``core/`` (vectorisation, caching,
dtype changes) can never silently shift them.
"""
from __future__ import annotations

import math

import pytest

from core.pricing import bs
from services import bs_pricer  # via the re-export shim

# Fixed reference points. Values computed with the R6 implementation and
# locked in — regenerate only if the pricing formula itself changes.
SCALAR_CASES = [
    # (F, K, T, sigma, right, expected_price)
    (1.08, 1.08, 30 / 365, 0.075, "C", 0.009264050499375953),
    (1.08, 1.09, 60 / 365, 0.075, "P", 0.01876168266401279),
    (1.25, 1.20, 90 / 365, 0.09, "C", 0.055362220302740295),
]


@pytest.mark.unit
@pytest.mark.parametrize("F,K,T,sigma,right,expected", SCALAR_CASES)
def test_bs_price_snapshot(F, K, T, sigma, right, expected):
    assert bs.bs_price(F, K, T, sigma, right) == pytest.approx(expected, rel=1e-9)


@pytest.mark.unit
def test_bs_delta_call_atm_is_near_half():
    delta = bs.bs_delta(F=1.08, K=1.08, T=30 / 365, sigma=0.075, right="C")
    assert delta == pytest.approx(0.5043, abs=1e-3)


@pytest.mark.unit
def test_bs_put_call_parity():
    F, K, T, sigma = 1.10, 1.12, 60 / 365, 0.08
    c = bs.bs_price(F, K, T, sigma, "C")
    p = bs.bs_price(F, K, T, sigma, "P")
    # Forward parity : C - P = F - K (undiscounted).
    assert c - p == pytest.approx(F - K, abs=1e-10)


@pytest.mark.unit
def test_bs_vega_matches_finite_difference():
    F, K, T, sigma = 1.08, 1.10, 90 / 365, 0.07
    analytical = bs.bs_vega(F, K, T, sigma)
    h = 1e-5
    numerical = (bs.bs_price(F, K, T, sigma + h, "C") - bs.bs_price(F, K, T, sigma - h, "C")) / (2 * h)
    assert analytical == pytest.approx(numerical, rel=1e-4)


@pytest.mark.unit
def test_degenerate_inputs_return_zero():
    assert bs.bs_price(1.0, 1.0, 0.0, 0.1, "C") == 0.0
    assert bs.bs_price(1.0, 1.0, 30 / 365, 0.0, "C") == 0.0
    assert bs.bs_delta(1.0, 1.0, 30 / 365, -0.1, "C") == 0.0
    assert bs.bs_gamma(0.0, 1.0, 30 / 365, 0.1) == 0.0


@pytest.mark.unit
def test_reexport_shim_is_identical_to_core():
    """services.bs_pricer must forward to core.pricing.bs — not fork."""
    assert bs_pricer.bs_price is bs.bs_price
    assert bs_pricer.bs_delta is bs.bs_delta
    assert bs_pricer.bs_gamma is bs.bs_gamma
    assert bs_pricer.bs_vega is bs.bs_vega
    assert bs_pricer.bs_theta is bs.bs_theta
    assert bs_pricer.interpolate_iv is bs.interpolate_iv


@pytest.mark.unit
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


@pytest.mark.unit
def test_d1_d2_shape_matches_textbook():
    """Spot-check d1 / d2 against the standard formula for one deterministic case."""
    F, K, T, sigma = 1.0, 1.0, 1.0, 0.2
    d1_expected = 0.5 * sigma * math.sqrt(T)
    # Recovered via call delta = N(d1).
    from scipy.stats import norm

    d1 = norm.ppf(bs.bs_delta(F, K, T, sigma, "C"))
    assert d1 == pytest.approx(d1_expected, abs=1e-9)
