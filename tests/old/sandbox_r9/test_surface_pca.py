"""Tests for core.vol.surface_pca — surface flattening, PCA fit + project + z-score."""
from __future__ import annotations

import numpy as np
import pytest


def _surface(atm_level: float, skew: float = 0.0, slope: float = 0.0):
    """Build a synthetic surface with level, skew and term-slope knobs."""
    from core.vol.surface_pca import DEFAULT_PILLARS, DEFAULT_TENORS

    surface: dict = {}
    for ti, tenor in enumerate(DEFAULT_TENORS):
        pillar_group = {}
        atm = atm_level + slope * ti
        for pi, pillar in enumerate(DEFAULT_PILLARS):
            # 10P is pi=0, ATM is pi=2, 10C is pi=4 — smile adds skew on put wing.
            offset_by_pi = {0: skew + 0.010, 1: skew * 0.4 + 0.003, 3: 0.002, 4: 0.008}
            offset = offset_by_pi.get(pi, 0.0)
            pillar_group[pillar] = {"iv": atm + offset, "strike": 1.17}
        surface[tenor] = pillar_group
    return surface


def test_flatten_surface_layout_is_tenor_major() -> None:
    from core.vol.surface_pca import (
        DEFAULT_PILLARS,
        DEFAULT_TENORS,
        N_FEATURES,
        flatten_surface,
    )

    surface = _surface(atm_level=0.06)
    v = flatten_surface(surface)
    assert v is not None
    assert v.shape == (N_FEATURES,)
    # First 5 cells = 1M × pillars.
    atm_1m_idx = DEFAULT_TENORS.index("1M") * len(DEFAULT_PILLARS) + DEFAULT_PILLARS.index("atm")
    assert v[atm_1m_idx] == pytest.approx(6.0)


def test_pca_fit_returns_none_on_empty_input() -> None:
    from core.vol.surface_pca import fit_pca

    assert fit_pca([]) is None


def test_pca_fit_flags_bootstrap_below_threshold() -> None:
    from core.vol.surface_pca import fit_pca

    surfaces = [_surface(atm_level=0.06 + 0.001 * i) for i in range(10)]
    model = fit_pca(surfaces)
    assert model is not None
    assert model.bootstrap is True  # 10 < 50 minimum


def test_pca_fit_level_factor_dominates_when_only_level_varies() -> None:
    from core.vol.surface_pca import fit_pca

    # 60 surfaces differing only in ATM level.
    surfaces = [_surface(atm_level=0.05 + 0.01 * np.random.rand()) for _ in range(60)]
    model = fit_pca(surfaces, n_components=3)
    assert model is not None
    assert model.bootstrap is False
    # PC1 should explain the vast majority of variance.
    assert model.explained_variance_ratio[0] > 0.85
    # And its loadings should be same-sign across all cells → label "level".
    labels = []
    from core.vol.surface_pca import label_pc
    labels = label_pc(model)
    assert labels[0] == "level"


def test_pca_project_scores_track_input_changes() -> None:
    from core.vol.surface_pca import fit_pca, project_surface

    rng = np.random.default_rng(7)
    surfaces = [_surface(atm_level=0.06 + rng.normal(0, 0.005)) for _ in range(80)]
    model = fit_pca(surfaces, n_components=3)
    assert model is not None
    low_surface = _surface(atm_level=0.04)
    high_surface = _surface(atm_level=0.08)
    score_low = project_surface(low_surface, model)
    score_high = project_surface(high_surface, model)
    # PC1 (level) should move in opposite directions for low vs high surfaces.
    assert score_low[0] != score_high[0]
    assert abs(score_low[0] - score_high[0]) > 1.0  # vol pts — meaningful spread


def test_compute_pc_signals_bootstrap_when_history_short() -> None:
    from core.vol.surface_pca import compute_pc_signals, fit_pca

    surfaces = [_surface(atm_level=0.06 + 0.001 * i) for i in range(60)]
    model = fit_pca(surfaces, n_components=3)
    assert model is not None
    # Current score with only 10 historical points → bootstrap.
    sigs = compute_pc_signals(
        current_scores=np.array([1.0, 0.0, 0.0]),
        historical_scores=[np.array([0.0, 0.0, 0.0])] * 10,
        model=model, min_history=50,
    )
    assert all(s.bootstrap for s in sigs)
    assert all(s.z == 0.0 for s in sigs)


def test_compute_pc_signals_z_score_fires_on_deviation() -> None:
    from core.vol.surface_pca import compute_pc_signals, fit_pca

    rng = np.random.default_rng(0)
    surfaces = [_surface(atm_level=0.06 + rng.normal(0, 0.002)) for _ in range(80)]
    model = fit_pca(surfaces, n_components=3)
    assert model is not None
    # Build 60 historical PC scores close to zero, current score way off.
    hist = [np.array([rng.normal(0, 0.5), 0.0, 0.0]) for _ in range(60)]
    sigs = compute_pc_signals(
        current_scores=np.array([3.0, 0.0, 0.0]),
        historical_scores=hist, model=model, min_history=50,
    )
    assert sigs[0].bootstrap is False
    assert abs(sigs[0].z) > 2.0  # 3σ-ish against near-zero mean, 0.5-std dist
