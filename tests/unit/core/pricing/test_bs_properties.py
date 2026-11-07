"""Property + finite-difference suite for the pricing kernel (#309, audit 02#2 /
02#3 / QM-01 / QM-14).

Hypothesis draws the inputs; every greek is checked against a central
difference of the price, every price against its no-arbitrage bounds, and
the vectorised pricer against the scalar one on the same draws including
the guarded elements (F <= 0, NaN).
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from core.pricing import bs
from core.risk.greeks import bs_price_vec

# Interior of the domain: away from the boundary, so finite differences are
# well conditioned. EURUSD-like forwards and strikes, 1 week to 2 years, 2 % to 40 % vol.
F_ = st.floats(min_value=0.9, max_value=1.4)
K_ = st.floats(min_value=0.9, max_value=1.4)
T_ = st.floats(min_value=7 / 365, max_value=2.0)
S_ = st.floats(min_value=0.02, max_value=0.40)
RIGHT = st.sampled_from(["C", "P"])

SETTINGS = settings(max_examples=150, deadline=None)


@SETTINGS
@given(F=F_, K=K_, T=T_, sigma=S_, right=RIGHT)
def test_price_within_no_arbitrage_bounds(F: float, K: float, T: float, sigma: float, right: str) -> None:
    p = bs.bs_price(F, K, T, sigma, right)
    intrinsic = max(F - K, 0.0) if right == "C" else max(K - F, 0.0)
    assert p >= intrinsic - 1e-12
    assert p <= (F if right == "C" else K) + 1e-12
    assert isinstance(p, float) and math.isfinite(p)


@SETTINGS
@given(F=F_, K=K_, T=T_, sigma=S_)
def test_put_call_parity_and_delta_bounds(F: float, K: float, T: float, sigma: float) -> None:
    c, p = bs.bs_price(F, K, T, sigma, "C"), bs.bs_price(F, K, T, sigma, "P")
    assert c - p == pytest.approx(F - K, abs=1e-9)
    dc, dp = bs.bs_delta(F, K, T, sigma, "C"), bs.bs_delta(F, K, T, sigma, "P")
    assert 0.0 <= dc <= 1.0 and -1.0 <= dp <= 0.0
    assert dc - dp == pytest.approx(1.0, abs=1e-12)
    assert bs.bs_gamma(F, K, T, sigma) >= 0.0 and bs.bs_vega(F, K, T, sigma) >= 0.0
    assert bs.bs_theta(F, K, T, sigma, "C") <= 1e-12


@SETTINGS
@given(F=F_, K=K_, T=T_, sigma=S_)
def test_price_is_monotone_in_vol_and_time(F: float, K: float, T: float, sigma: float) -> None:
    for right in ("C", "P"):
        assert bs.bs_price(F, K, T, sigma * 1.1, right) >= bs.bs_price(F, K, T, sigma, right) - 1e-12
        assert bs.bs_price(F, K, T * 1.1, sigma, right) >= bs.bs_price(F, K, T, sigma, right) - 1e-12


def _meaningful(F: float, K: float, T: float, sigma: float) -> bool:
    """Finite differences only make sense where the option has value and
    vega: a 1e-12 deep-OTM price is all rounding. Two standard deviations
    of moneyness is the honest domain for a central-difference check."""
    return abs(math.log(F / K)) <= 2.0 * sigma * math.sqrt(T) and bs.bs_vega(F, K, T, sigma) > 1e-4


@SETTINGS
@given(F=F_, K=K_, T=T_, sigma=S_, right=RIGHT)
def test_delta_gamma_theta_match_finite_differences(F: float, K: float, T: float, sigma: float, right: str) -> None:
    assume(_meaningful(F, K, T, sigma))
    # Step = a small fraction of one standard deviation of the forward, so the
    # O(h^2) truncation error scales with the curvature at every draw.
    h = 1e-3 * F * sigma * math.sqrt(T)
    up, dn, mid = (bs.bs_price(F + h, K, T, sigma, right), bs.bs_price(F - h, K, T, sigma, right),
                   bs.bs_price(F, K, T, sigma, right))
    fd_delta = (up - dn) / (2 * h)
    fd_gamma = (up - 2 * mid + dn) / (h * h)
    assert bs.bs_delta(F, K, T, sigma, right) == pytest.approx(fd_delta, rel=1e-3, abs=1e-5)
    assert bs.bs_gamma(F, K, T, sigma) == pytest.approx(fd_gamma, rel=2e-3, abs=1e-4)
    # theta per day = -dP/dT / 365 (undiscounted: identical for call and put)
    ht = T * 1e-3
    fd_theta_day = -(bs.bs_price(F, K, T + ht, sigma, right) - bs.bs_price(F, K, T - ht, sigma, right)) / (2 * ht) / 365.0
    assert bs.bs_theta(F, K, T, sigma, right) == pytest.approx(fd_theta_day, rel=2e-3, abs=1e-8)


@SETTINGS
@given(F=F_, K=K_, T=T_, sigma=S_)
def test_vega_vanna_volga_match_finite_differences(F: float, K: float, T: float, sigma: float) -> None:
    assume(_meaningful(F, K, T, sigma))
    h = sigma * 1e-3
    fd_vega = (bs.bs_price(F, K, T, sigma + h, "C") - bs.bs_price(F, K, T, sigma - h, "C")) / (2 * h)
    assert bs.bs_vega(F, K, T, sigma) == pytest.approx(fd_vega, rel=2e-3, abs=1e-7)
    fd_volga = (bs.bs_vega(F, K, T, sigma + h) - bs.bs_vega(F, K, T, sigma - h)) / (2 * h)
    assert bs.bs_volga(F, K, T, sigma) == pytest.approx(fd_volga, rel=5e-3, abs=1e-5)
    hf = 1e-3 * F * sigma * math.sqrt(T)
    fd_vanna = (bs.bs_vega(F + hf, K, T, sigma) - bs.bs_vega(F - hf, K, T, sigma)) / (2 * hf)
    assert bs.bs_vanna(F, K, T, sigma) == pytest.approx(fd_vanna, rel=5e-3, abs=1e-5)


@SETTINGS
@given(F=F_, K=K_, T=T_, sigma=S_, right=RIGHT)
def test_implied_vol_round_trips(F: float, K: float, T: float, sigma: float, right: str) -> None:
    price = bs.bs_price(F, K, T, sigma, right)
    intrinsic = max(F - K, 0.0) if right == "C" else max(K - F, 0.0)
    if price - intrinsic < 1e-7:
        return  # no vega left to invert on: the solver is entitled to give up
    iv = bs.bs_implied_vol(price, F, K, T, right)
    assert iv is not None and iv == pytest.approx(sigma, abs=2e-4)


@SETTINGS
@given(Fs=st.lists(st.floats(min_value=0.9, max_value=1.4), min_size=1, max_size=12), K=K_, T=T_, sigma=S_, right=RIGHT)
def test_vector_equals_scalar_pointwise(Fs: list[float], K: float, T: float, sigma: float, right: str) -> None:
    F_arr = np.array(Fs)
    vec = bs_price_vec(F_arr, K, T, sigma, right)
    scalar = np.array([bs.bs_price(F, K, T, sigma, right) for F in Fs])
    np.testing.assert_allclose(vec, scalar, rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize("right", ["C", "P"])
def test_vector_boundary_and_guards(right: str) -> None:
    F_arr = np.array([1.05, 1.10, 1.15])
    K = 1.10
    # expiry / zero vol: the intrinsic vector, not zeros (QM-01)
    want = np.maximum(F_arr - K, 0) if right == "C" else np.maximum(K - F_arr, 0)
    np.testing.assert_allclose(bs_price_vec(F_arr, K, 0.0, 0.08, right), want)
    np.testing.assert_allclose(bs_price_vec(F_arr, K, 30 / 365, 0.0, right), want)
    # a bad forward element prices 0 there and leaves the others intact (QM-14)
    bad = np.array([1.05, 0.0, -1.0, float("nan"), 1.15])
    out = bs_price_vec(bad, K, 30 / 365, 0.08, right)
    assert np.isfinite(out).all() and out[1] == out[2] == out[3] == 0.0
    assert out[0] == pytest.approx(bs.bs_price(1.05, K, 30 / 365, 0.08, right))
    assert out[4] == pytest.approx(bs.bs_price(1.15, K, 30 / 365, 0.08, right))
    # not an option at all
    assert np.all(bs_price_vec(F_arr, 0.0, 30 / 365, 0.08, right) == 0.0)
