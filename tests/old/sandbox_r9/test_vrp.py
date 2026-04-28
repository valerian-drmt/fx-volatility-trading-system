"""Tests for core.vol.vrp — VRP lookup, regime detection, Q-measure conversion."""
from __future__ import annotations

import pytest


def test_predict_vrp_calm_returns_positive_and_tenor_aware() -> None:
    from core.vol.vrp import predict_vrp

    vrp_1m = predict_vrp("1M", "calm")
    vrp_6m = predict_vrp("6M", "calm")
    assert vrp_1m.value_vol_pts > 0
    assert vrp_6m.value_vol_pts >= vrp_1m.value_vol_pts   # calm = mildly upward


def test_predict_vrp_unknown_tenor_logs_warning_and_uses_fallback() -> None:
    from core.vol.vrp import predict_vrp

    sig = predict_vrp("99Y", "calm")
    assert sig.value_vol_pts > 0  # non-zero fallback
    assert sig.tenor == "99Y"


def test_q_measure_is_p_plus_vrp() -> None:
    from core.vol.vrp import q_measure_from_p

    q, vrp = q_measure_from_p(5.8, tenor="1M", regime="calm")
    assert q > 5.8
    assert q == pytest.approx(5.8 + vrp)
    assert vrp > 0


def test_detect_regime_defaults_to_calm() -> None:
    from core.vol.vrp import detect_regime

    assert detect_regime() == "calm"
    assert detect_regime(vol_level_pct=6.0) == "calm"


def test_detect_regime_stressed_on_high_level() -> None:
    from core.vol.vrp import detect_regime

    assert detect_regime(vol_level_pct=12.0) == "stressed"


def test_detect_regime_pre_event_on_sloped_series() -> None:
    from core.vol.vrp import detect_regime

    # High vol level + strong negative term slope = pre-event regime.
    r = detect_regime(vol_level_pct=8.0, term_slope_pct=-3.0)
    assert r == "pre_event"


def test_compute_realized_vrp_aligns_forward_window() -> None:
    from core.vol.vrp import compute_realized_vrp

    # IV timestamps at day 0, 1, 2 ; RV timestamps at day 30, 31, 32.
    day = 86400.0
    iv_hist = [(0.0, 6.0), (day, 6.2), (2 * day, 5.9)]
    rv_hist = [(30 * day, 5.5), (31 * day, 5.6), (32 * day, 5.7)]

    out = compute_realized_vrp(iv_hist, rv_hist, horizon_days=30)
    assert len(out) == 3
    # First point : IV at day 0 matched against RV at day 30 (≥ 30*day).
    ts, vrp = out[0]
    assert ts == 0.0
    assert vrp == pytest.approx(6.0 - 5.5)
