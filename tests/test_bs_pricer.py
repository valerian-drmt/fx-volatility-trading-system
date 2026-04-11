"""Unit tests for Black-Scholes pricer functions."""

import pytest

from services.bs_pricer import bs_delta, bs_gamma, bs_price, bs_theta, bs_vega, interpolate_iv

# Reference values: F=1.10, K=1.10, T=0.25, sigma=0.08 (ATM, 3M, 8% vol)
F, K, T, SIGMA = 1.10, 1.10, 0.25, 0.08


@pytest.mark.unit
class TestBsPrice:
    def test_call_positive(self):
        p = bs_price(F, K, T, SIGMA, "C")
        assert p > 0

    def test_put_positive(self):
        p = bs_price(F, K, T, SIGMA, "P")
        assert p > 0

    def test_put_call_parity(self):
        """Undiscounted: C - P = F - K."""
        c = bs_price(F, K, T, SIGMA, "C")
        p = bs_price(F, K, T, SIGMA, "P")
        assert abs((c - p) - (F - K)) < 1e-10

    def test_returns_zero_sigma_zero(self):
        assert bs_price(F, K, T, 0.0, "C") == 0.0

    def test_returns_zero_T_zero(self):
        assert bs_price(F, K, 0.0, SIGMA, "C") == 0.0

    def test_returns_zero_F_zero(self):
        assert bs_price(0.0, K, T, SIGMA, "C") == 0.0

    def test_returns_zero_K_zero(self):
        assert bs_price(F, 0.0, T, SIGMA, "C") == 0.0


@pytest.mark.unit
class TestBsDelta:
    def test_call_between_0_and_1(self):
        d = bs_delta(F, K, T, SIGMA, "C")
        assert 0 < d < 1

    def test_put_between_minus1_and_0(self):
        d = bs_delta(F, K, T, SIGMA, "P")
        assert -1 < d < 0

    def test_atm_call_near_half(self):
        d = bs_delta(F, K, T, SIGMA, "C")
        assert abs(d - 0.5) < 0.05

    def test_returns_zero_on_bad_inputs(self):
        assert bs_delta(F, K, T, 0.0, "C") == 0.0


@pytest.mark.unit
class TestBsGamma:
    def test_positive(self):
        g = bs_gamma(F, K, T, SIGMA)
        assert g > 0

    def test_returns_zero_on_bad_inputs(self):
        assert bs_gamma(F, K, 0.0, SIGMA) == 0.0


@pytest.mark.unit
class TestBsVega:
    def test_positive(self):
        v = bs_vega(F, K, T, SIGMA)
        assert v > 0

    def test_returns_zero_on_bad_inputs(self):
        assert bs_vega(0.0, K, T, SIGMA) == 0.0


@pytest.mark.unit
class TestBsTheta:
    def test_call_negative(self):
        th = bs_theta(F, K, T, SIGMA, "C")
        assert th < 0

    def test_returns_zero_on_bad_inputs(self):
        assert bs_theta(F, K, 0.0, SIGMA, "C") == 0.0


@pytest.mark.unit
class TestInterpolateIv:
    SURFACE = {
        "1M": {
            "sigma_ATM_pct": 7.0, "strike_atm": 1.10,
            "iv_25dp_pct": 7.5, "strike_25dp": 1.08,
            "iv_25dc_pct": 6.8, "strike_25dc": 1.12,
            "iv_10dp_pct": 8.0, "strike_10dp": 1.06,
            "iv_10dc_pct": 6.5, "strike_10dc": 1.14,
        }
    }

    def test_returns_iv_for_atm_strike(self):
        iv = interpolate_iv(self.SURFACE, "1M", 1.10, 1.10)
        assert iv is not None
        assert abs(iv - 0.07) < 0.01

    def test_returns_none_for_missing_tenor(self):
        iv = interpolate_iv(self.SURFACE, "6M", 1.10, 1.10)
        assert iv is None

    def test_returns_closest_strike(self):
        iv = interpolate_iv(self.SURFACE, "1M", 1.085, 1.10)
        assert iv is not None
        assert 0.06 < iv < 0.09

    def test_empty_surface(self):
        iv = interpolate_iv({}, "1M", 1.10, 1.10)
        assert iv is None
