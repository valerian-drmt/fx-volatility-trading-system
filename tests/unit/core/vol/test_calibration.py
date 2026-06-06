"""Tests for core.vol.calibration — W₁ closed form + VRP empirical stats."""
from __future__ import annotations

import pytest


def test_w1_returns_default_on_short_history() -> None:
    from core.vol.calibration import calibrate_w1_closed_form

    est = calibrate_w1_closed_form([6.0], [5.5], [5.8], default=0.65)
    assert est.bootstrap is True
    assert est.value == 0.65
    assert est.source == "default"


def test_w1_closed_form_recovers_true_weight() -> None:
    from core.vol.calibration import calibrate_w1_closed_form

    # Synthetic : realised = W₁·Anchor + (1−W₁)·GARCH with W₁ = 0.3.
    true_w = 0.3
    anchors = [5.8 + 0.01 * i for i in range(50)]
    garch = [6.5 + 0.02 * i for i in range(50)]
    realised = [true_w * a + (1 - true_w) * g for a, g in zip(anchors, garch, strict=True)]
    est = calibrate_w1_closed_form(anchors, garch, realised)
    assert est.bootstrap is False
    assert est.source == "empirical"
    assert est.value == pytest.approx(true_w, abs=0.01)


def test_w1_clips_to_unit_interval() -> None:
    from core.vol.calibration import calibrate_w1_closed_form

    # Realised far above both anchors and garch → optimiser pushes W₁ > 1, clipped.
    anchors = [5.5] * 40
    garch = [5.0] * 40
    realised = [10.0] * 40
    est = calibrate_w1_closed_form(anchors, garch, realised)
    assert 0.0 <= est.value <= 1.0


def test_vrp_per_tenor_returns_default_below_threshold() -> None:
    from core.vol.calibration import calibrate_vrp_empirical

    out = calibrate_vrp_empirical({"1M": [0.5] * 10})
    assert out["1M"].bootstrap is True
    assert out["1M"].source == "default"


def test_vrp_per_tenor_calibrates_with_enough_samples() -> None:
    from core.vol.calibration import calibrate_vrp_empirical

    out = calibrate_vrp_empirical({"1M": [0.5 + 0.01 * i for i in range(70)]})
    assert out["1M"].bootstrap is False
    assert out["1M"].mean > 0.5
    assert out["1M"].std > 0.0


def test_vrp_oos_diagnostic_beats_constant_baseline_on_good_predictions() -> None:
    from core.vol.calibration import evaluate_vrp_model_oos

    # Perfect predictor → MAE ≈ 0, improvement_vs_const ≈ 1.0.
    y = [0.5, 0.6, 0.7, 0.8]
    diag = evaluate_vrp_model_oos(y_true=y, y_pred=y)
    assert diag["mae"] == pytest.approx(0.0)
    assert diag["improvement_vs_const"] == pytest.approx(1.0)
