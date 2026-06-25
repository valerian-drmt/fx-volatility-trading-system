"""Unit tests for Step 2 PCA pure helpers (no DB)."""
from __future__ import annotations

import numpy as np
import pytest

from core.vol.pca_engine import (
    DELTAS,
    MIN_CUMULATIVE_VARIANCE,
    MIN_N_OBS_HARD,
    N_FEATURES,
    TENORS,
    actionable_check,
    check_coherence,
    classify_label,
    classify_strength,
    feature_vector_from_surface,
    fit_pca_svd,
    is_persistent,
    pc3_sub_metrics,
    project,
    reason_category,
    sign_correct_loadings,
    zscore_against,
)


def _synthetic_X(n_obs: int = 200, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.normal(size=(n_obs, 3))
    # Linterman-Scheinkman style : 3 latent factors → 30 features
    L = rng.normal(size=(3, N_FEATURES))
    return base @ L + 7.0 + 0.05 * rng.normal(size=(n_obs, N_FEATURES))


def test_fit_deterministic():
    X = _synthetic_X(seed=1)
    a = fit_pca_svd(X)
    b = fit_pca_svd(X)
    assert np.allclose(a.loadings, b.loadings)
    assert np.allclose(a.variance_explained_ratio, b.variance_explained_ratio)


def test_fit_var_ratio_sorted_desc():
    fit = fit_pca_svd(_synthetic_X())
    diffs = np.diff(fit.variance_explained_ratio)
    assert (diffs <= 1e-10).all()


def test_fit_top3_capture_most_variance_synthetic():
    fit = fit_pca_svd(_synthetic_X())
    assert fit.variance_explained_ratio[:3].sum() > 0.95


def test_sign_correct_handles_flip():
    fit1 = fit_pca_svd(_synthetic_X(seed=1))
    flipped = fit1.loadings.copy()
    flipped[1] = -flipped[1]
    corrected, cos, flips = sign_correct_loadings(flipped, fit1.loadings)
    assert flips[1] is True
    assert np.allclose(corrected[1], fit1.loadings[1])
    assert cos[1] > 0.99


def test_sign_correct_first_fit_no_reference():
    fit = fit_pca_svd(_synthetic_X())
    corrected, cos, flips = sign_correct_loadings(fit.loadings, None)
    assert all(not f for f in flips)
    assert all(not np.isfinite(c) for c in cos)


def test_project_round_trip():
    fit = fit_pca_svd(_synthetic_X(seed=2))
    x = _synthetic_X(seed=99)[0]
    scores = project(x, fit.means, fit.stds, fit.loadings)
    assert scores.shape == (6,)


def test_zscore_returns_zero_when_insufficient():
    assert zscore_against(1.0, []) == 0.0
    assert zscore_against(1.0, [0.0, 0.1, 0.2]) == 0.0


def test_zscore_basic():
    z = zscore_against(3.0, [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 0.0, 0.5])
    assert z > 1.0


@pytest.mark.parametrize("z,expected", [
    (0.5, "FAIR"), (-0.5, "FAIR"),
    (2.0, "EXPENSIVE"), (-2.0, "CHEAP"),
    (1.4, "FAIR"), (1.5, "EXPENSIVE"),
])
def test_classify_label(z, expected):
    assert classify_label(z) == expected


def test_classify_strength_levels():
    assert classify_strength(0.5) is None
    assert classify_strength(1.2) == "weak"
    assert classify_strength(1.8) == "moderate"
    assert classify_strength(2.5) == "strong"
    assert classify_strength(3.5) == "extreme"


def test_actionable_blocks_unstable_loadings():
    f = actionable_check(
        pc_id=1, z_score=2.0, label="EXPENSIVE",
        loadings_stable=False, variance_explained=0.8, persistent=True,
    )
    assert not f.actionable
    assert "loadings_unstable" in f.reason


def test_actionable_blocks_low_variance():
    f = actionable_check(
        pc_id=1, z_score=2.0, label="EXPENSIVE",
        loadings_stable=True, variance_explained=0.3, persistent=True,
    )
    assert not f.actionable
    assert "low_variance" in f.reason


def test_actionable_blocks_below_threshold():
    f = actionable_check(
        pc_id=1, z_score=0.5, label="FAIR",
        loadings_stable=True, variance_explained=0.7, persistent=True,
    )
    assert not f.actionable


def test_actionable_blocks_not_persistent():
    f = actionable_check(
        pc_id=1, z_score=2.0, label="EXPENSIVE",
        loadings_stable=True, variance_explained=0.7, persistent=False,
    )
    assert not f.actionable
    assert f.reason == "signal_not_persistent"


def test_actionable_passes_all_gates():
    f = actionable_check(
        pc_id=1, z_score=2.0, label="EXPENSIVE",
        loadings_stable=True, variance_explained=0.7, persistent=True,
    )
    assert f.actionable
    assert f.strength in {"moderate", "strong"}


def test_check_coherence_no_contradiction():
    sigs = {"pc1": {"label": "CHEAP"}, "pc2": {"label": "FAIR"}, "pc3": {"label": "EXPENSIVE"}}
    c = check_coherence(sigs)
    assert c["all_coherent"] is True


def test_check_coherence_pc1_pc2_contradiction():
    sigs = {"pc1": {"label": "CHEAP"}, "pc2": {"label": "EXPENSIVE"}}
    c = check_coherence(sigs)
    assert c["all_coherent"] is False
    assert ("pc1", "pc2") in c["contradictions"]


def test_is_persistent_requires_n_consecutive():
    assert is_persistent([2.0, 1.5, 1.2]) is True
    assert is_persistent([2.0, -1.5, 1.2]) is False  # sign flip
    assert is_persistent([2.0, 0.5, 1.2]) is False  # below threshold
    assert is_persistent([2.0, 1.5]) is False  # too short


def test_feature_vector_from_surface_full():
    surface = {
        t: {d: {"iv": 0.06 + 0.001 * i + 0.0005 * j}
            for j, d in enumerate(["10dp", "25dp", "atm", "25dc", "10dc"])}
        for i, t in enumerate(TENORS)
    }
    x = feature_vector_from_surface(surface)
    assert x is not None and x.shape == (30,)


def test_feature_vector_from_surface_incomplete_returns_none():
    surface = {"1M": {"atm": {"iv": 0.06}}}  # missing tenors / deltas
    assert feature_vector_from_surface(surface) is None


def test_pc3_sub_metrics_flat_smile_zero():
    """Flat surface (constant IV) → skew = 0 and convex = 0."""
    x = np.full(N_FEATURES, 7.0)
    skew, convex = pc3_sub_metrics(x)
    assert skew == pytest.approx(0.0, abs=1e-12)
    assert convex == pytest.approx(0.0, abs=1e-12)


def test_pc3_sub_metrics_positive_risk_reversal():
    """25dc > 25dp → skew > 0."""
    x = np.zeros(N_FEATURES)
    for ti in range(len(TENORS)):
        x[ti * len(DELTAS) + DELTAS.index("25dp")] = 5.0
        x[ti * len(DELTAS) + DELTAS.index("25dc")] = 7.0  # 25dc - 25dp = +2
        x[ti * len(DELTAS) + DELTAS.index("atm")] = 6.0
        x[ti * len(DELTAS) + DELTAS.index("10dp")] = 5.5
        x[ti * len(DELTAS) + DELTAS.index("10dc")] = 6.5
    skew, _ = pc3_sub_metrics(x)
    assert skew == pytest.approx(2.0)


def test_pc3_sub_metrics_butterfly_convexity():
    """Wings rich (10dp + 10dc > 2·atm) → convex > 0."""
    x = np.zeros(N_FEATURES)
    for ti in range(len(TENORS)):
        x[ti * len(DELTAS) + DELTAS.index("10dp")] = 8.0
        x[ti * len(DELTAS) + DELTAS.index("25dp")] = 7.0
        x[ti * len(DELTAS) + DELTAS.index("atm")] = 6.0  # 10dp + 10dc - 2*atm = 8+8-12 = 4
        x[ti * len(DELTAS) + DELTAS.index("25dc")] = 7.0
        x[ti * len(DELTAS) + DELTAS.index("10dc")] = 8.0
    _, convex = pc3_sub_metrics(x)
    assert convex == pytest.approx(4.0)


def test_pc3_sub_metrics_wrong_shape_raises():
    with pytest.raises(ValueError):
        pc3_sub_metrics(np.zeros(29))


def test_actionable_blocks_low_n_obs():
    """n_obs < MIN_N_OBS_HARD → blocked even if all other gates green."""
    f = actionable_check(
        pc_id=1, z_score=2.0, label="EXPENSIVE",
        loadings_stable=True, variance_explained=0.7, persistent=True,
        n_obs=MIN_N_OBS_HARD - 1, cumulative_variance=0.95,
    )
    assert not f.actionable
    assert f.reason == "low_n_obs"


def test_actionable_blocks_low_total_variance():
    """cumulative_variance < MIN_CUMULATIVE_VARIANCE → blocked (noise floor)."""
    f = actionable_check(
        pc_id=1, z_score=2.0, label="EXPENSIVE",
        loadings_stable=True, variance_explained=0.7, persistent=True,
        n_obs=200, cumulative_variance=MIN_CUMULATIVE_VARIANCE - 0.01,
    )
    assert not f.actionable
    assert f.reason == "low_total_variance"


def test_actionable_n_obs_gate_takes_precedence():
    """When n_obs is low AND variance is low, n_obs is the reported reason
    (n_obs gate runs first — failing fits dominate)."""
    f = actionable_check(
        pc_id=2, z_score=2.0, label="EXPENSIVE",
        loadings_stable=False, variance_explained=0.05, persistent=False,
        n_obs=10, cumulative_variance=0.40,
    )
    assert not f.actionable
    assert f.reason == "low_n_obs"


def test_reason_category_known():
    assert reason_category("low_variance_pc1") == "variance"
    assert reason_category("loadings_unstable_pc2") == "stability"
    assert reason_category("signal_below_threshold") == "magnitude"
    assert reason_category("signal_not_persistent") == "persistence"
    assert reason_category("low_n_obs") == "n_obs"
    assert reason_category("low_total_variance") == "variance"


def test_reason_category_none():
    assert reason_category(None) is None


def test_reason_category_unknown_falls_to_other():
    assert reason_category("totally_made_up") == "other"
