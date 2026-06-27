"""Unit tests for core.risk.greek_limits (spec §9 acceptance criteria)."""
from __future__ import annotations

import pytest

from core.risk.greek_limits import compute_caps, ewma, nav_base, regime_mult


def test_caps_match_spec_sanity_values():
    # §9: NAV=812k, S=1.08, regime_mult=1 → delta≈243.6k, vega≈5075, gamma≈3007 (±1%).
    c = compute_caps(812_000, 1.08, 1.0)
    assert c.delta_usd == pytest.approx(243_600, rel=0.01)
    assert c.vega_usd == pytest.approx(5_075, rel=0.01)
    assert c.gamma_pip == pytest.approx(3_007, rel=0.01)
    assert c.loss_budget_usd == pytest.approx(0.05 * 812_000)


def test_caps_scale_inversely_with_shocks_via_regime():
    base = compute_caps(812_000, 1.08, 1.0)
    hot = compute_caps(812_000, 1.08, 2.0)
    # delta, vega ∝ 1/s, 1/v → halve when regime doubles the shocks.
    assert hot.delta_usd == pytest.approx(base.delta_usd / 2, rel=1e-9)
    assert hot.vega_usd == pytest.approx(base.vega_usd / 2, rel=1e-9)
    # gamma ∝ 1/s^2 → quarter when regime doubles.
    assert hot.gamma_pip == pytest.approx(base.gamma_pip / 4, rel=1e-9)


def test_caps_scale_linearly_with_nav_base():
    small = compute_caps(400_000, 1.08, 1.0)
    big = compute_caps(800_000, 1.08, 1.0)
    assert big.delta_usd == pytest.approx(2 * small.delta_usd)
    assert big.vega_usd == pytest.approx(2 * small.vega_usd)
    assert big.gamma_pip == pytest.approx(2 * small.gamma_pip)


def test_caps_degrade_to_zero_on_bad_inputs():
    assert compute_caps(0, 1.08, 1.0).delta_usd == 0.0
    assert compute_caps(812_000, 0, 1.0).gamma_pip == 0.0
    assert compute_caps(812_000, 1.08, 0).vega_usd == 0.0


def test_ewma_weights_recent_higher_and_handles_empty():
    assert ewma([], 20) is None
    # flat series → its own value.
    assert ewma([100.0, 100.0, 100.0], 20) == pytest.approx(100.0)
    # rising series → EWMA above the simple mean (recent weighted more).
    rising = [float(x) for x in range(1, 11)]
    assert ewma(rising, 5) > sum(rising) / len(rising)


def test_nav_base_floors_on_high_water_mark():
    # A drawdown tail must NOT drag nav_base down to the live trough (§6):
    # HWM*0.9 floor dominates the EWMA of the depressed recent values.
    series = [1_000_000] * 10 + [700_000] * 5
    nb = nav_base(series)
    assert nb is not None
    assert nb >= 1_000_000 * 0.9          # floored at 90% of the peak
    assert nb > min(series)                # never the trough
    assert nav_base([]) is None


def test_nav_base_floored_against_single_drawdown_print():
    # §9 intent: a fresh drawdown must not collapse nav_base to the live trough.
    # The 0.9*HWM floor + EWMA inertia keep it sticky (the endpoint feeds a daily
    # series, so within one session nav_base does not move at all).
    pre = [1_000_000] * 20
    post = [*pre, 600_000]                  # one fresh drawdown print
    nb_pre, nb_post = nav_base(pre), nav_base(post)
    assert nb_post >= 1_000_000 * 0.9       # floor holds
    assert nb_post >= 0.95 * nb_pre         # barely moves on one print
    assert nb_post > 600_000                # never the trough


def test_regime_mult_clamped_and_safe():
    assert regime_mult(None, 8.0) == 1.0
    assert regime_mult(8.0, 0) == 1.0
    assert regime_mult(8.0, 8.0) == pytest.approx(1.0)
    assert regime_mult(24.0, 8.0) == pytest.approx(3.0)   # clamped to hi
    assert regime_mult(16.0, 8.0) == pytest.approx(2.0)
    assert regime_mult(4.0, 8.0) == 1.0                   # clamped to lo (never <1)
