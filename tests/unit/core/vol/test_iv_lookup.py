"""core.vol.iv_lookup — the one IV accessor (#310, audit QM-02 / R3-02).

The surface below is the shape the vol engine actually publishes
(``latest_vol_surface:EURUSD`` → ``payload["surface"]``): nested cells with a
decimal ``iv`` and a ``strike``; interpolated tenors carry ``strike: None``.
"""
from __future__ import annotations

import math

import pytest

from core.pricing.bs import bs_delta, interpolate_iv
from core.vol.iv_lookup import iv_at_strike, strike_from_delta

SURFACE = {
    "1M": {
        "10dp": {"iv": 0.090, "strike": 1.060, "source": "listed"},
        "25dp": {"iv": 0.081, "strike": 1.070, "source": "listed"},
        "atm":  {"iv": 0.075, "strike": 1.080, "source": "listed"},
        "25dc": {"iv": 0.078, "strike": 1.090, "source": "listed"},
        "10dc": {"iv": 0.084, "strike": 1.100, "source": "listed"},
    },
    # interpolated tenor as to_display_surface used to emit it: no strikes
    "4M": {
        "25dp": {"iv": 0.082, "strike": None, "source": "interp"},
        "atm":  {"iv": 0.077, "strike": None, "source": "interp"},
        "25dc": {"iv": 0.079, "strike": None, "source": "interp"},
    },
    "_svi": {"1M": {"a": 0.0}},  # meta key, must be ignored
}


def test_real_published_shape_is_read_and_interpolated_linearly_in_strike() -> None:
    res = iv_at_strike(SURFACE, "1M", 1.075)
    assert res is not None
    assert res.source == "interp_strike"
    # halfway between 25dp (0.081 @1.070) and atm (0.075 @1.080)
    assert res.iv == pytest.approx(0.078, abs=1e-12)
    assert res.n_points == 5


def test_exact_pillar_strike_returns_that_pillar() -> None:
    res = iv_at_strike(SURFACE, "1M", 1.080)
    assert res is not None and res.source == "pillar" and res.iv == 0.075


def test_beyond_the_wings_is_clamped_not_extrapolated() -> None:
    lo = iv_at_strike(SURFACE, "1M", 1.000)
    hi = iv_at_strike(SURFACE, "1M", 1.400)
    assert lo is not None and lo.source == "wing_clamp" and lo.iv == 0.090
    assert hi is not None and hi.source == "wing_clamp" and hi.iv == 0.084


def test_missing_tenor_or_empty_surface_is_none_not_a_constant() -> None:
    assert iv_at_strike(SURFACE, "9M", 1.08) is None
    assert iv_at_strike({}, "1M", 1.08) is None
    assert iv_at_strike(None, "1M", 1.08) is None
    assert iv_at_strike({"1M": {"atm": {"iv": None, "strike": 1.08}}}, "1M", 1.08) is None


def test_strikeless_interpolated_tenor_is_placed_from_delta_when_F_and_T_given() -> None:
    # Without F/T nothing can be placed (this is what sent the risk engine to 8 %).
    assert iv_at_strike(SURFACE, "4M", 1.08) is None
    res = iv_at_strike(SURFACE, "4M", 1.08, F=1.08, T=120 / 365)
    assert res is not None
    assert res.n_points == 3
    assert 0.077 <= res.iv <= 0.082


def test_strike_from_delta_inverts_bs_delta() -> None:
    F, T, sigma = 1.08, 0.25, 0.08
    for label, call_delta in (("25dc", 0.25), ("10dc", 0.10), ("25dp", 0.75), ("10dp", 0.90)):
        K = strike_from_delta(F, T, sigma, label)
        assert bs_delta(F, K, T, sigma, "C") == pytest.approx(call_delta, abs=1e-6)
    assert strike_from_delta(F, T, sigma, "atm") == F
    # put wing below the forward, call wing above
    assert strike_from_delta(F, T, sigma, "25dp") < F < strike_from_delta(F, T, sigma, "25dc")
    assert math.isfinite(strike_from_delta(F, 0.0, sigma, "25dc"))


def test_legacy_interpolate_iv_wrapper_now_reads_the_live_shape() -> None:
    """The bug: interpolate_iv read sigma_ATM_pct / strike_atm and returned None
    on every live surface, so n_surface_missing was never 0."""
    assert interpolate_iv(SURFACE, "1M", 1.075, F=1.08) == pytest.approx(0.078, abs=1e-12)
    assert interpolate_iv(SURFACE, "9M", 1.075, F=1.08) is None
    legacy_flat = {"1M": {"strike_atm": 1.08, "sigma_ATM_pct": 7.5}}
    assert interpolate_iv(legacy_flat, "1M", 1.08, F=1.08) is None  # retired schema, not guessed
