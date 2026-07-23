"""Sanity tests for ``core.vol.gmm_regime`` — fit, label mapping, inference.

New coverage (2026-07 remediation, plan 04 item 8): first-ever tests for
the 3-component GMM regime classifier.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from core.vol.gmm_regime import (
    MIN_OBS_GMM,
    deserialize_gmm,
    fit_gmm,
    infer_proba,
    serialize_gmm,
)

pytestmark = pytest.mark.unit

# Cluster means in (vol_level, vol_of_vol, term_slope) space — well
# separated even under the heavy reg_covar=0.5 regulariser.
CALM_MEAN = (5.0, 1.0, 0.5)
PRE_EVENT_MEAN = (9.0, 2.0, -3.0)
STRESSED_MEAN = (16.0, 5.0, 1.0)


def _three_cluster_data(n_per_cluster: int = 80, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    clusters = [
        rng.normal(mean, 0.8, size=(n_per_cluster, 3))
        for mean in (CALM_MEAN, PRE_EVENT_MEAN, STRESSED_MEAN)
    ]
    return np.vstack(clusters)


def test_fit_gmm_returns_none_below_min_obs():
    X = np.ones((MIN_OBS_GMM - 1, 3))
    assert fit_gmm(X) == (None, None)


def test_fit_gmm_returns_none_on_1d_input():
    assert fit_gmm(np.ones(100)) == (None, None)


def test_fit_gmm_maps_components_by_vol_level_ordering():
    X = _three_cluster_data()
    gmm, fit = fit_gmm(X)
    assert gmm is not None and fit is not None
    assert fit.converged is True
    assert fit.n_obs == X.shape[0]
    assert set(fit.component_to_label.values()) == {"calm", "pre_event", "stressed"}
    # The documented mapping: lowest mean vol_level ⇒ calm, highest ⇒
    # stressed, middle ⇒ pre_event — recompute from the fitted means.
    means = np.asarray(gmm.means_)
    order = np.argsort(means[:, 0])
    assert fit.component_to_label[int(order[0])] == "calm"
    assert fit.component_to_label[int(order[1])] == "pre_event"
    assert fit.component_to_label[int(order[2])] == "stressed"


def test_infer_proba_classifies_stressed_point():
    X = _three_cluster_data()
    gmm, fit = fit_gmm(X)
    assert gmm is not None and fit is not None
    result = infer_proba(gmm, np.array(STRESSED_MEAN), fit)
    assert result.label == "stressed"
    assert result.p_stressed > 0.5
    assert result.p_calm + result.p_stressed + result.p_pre_event == pytest.approx(1.0, abs=1e-3)


def test_fit_gmm_is_deterministic_across_refits():
    X = _three_cluster_data()
    _, fit_a = fit_gmm(X)
    _, fit_b = fit_gmm(X)
    assert fit_a is not None and fit_b is not None
    # GMM_RANDOM_STATE=42 pins the k-means init → identical mapping.
    assert fit_a.component_to_label == fit_b.component_to_label


def test_serialize_deserialize_roundtrip_preserves_predict_proba():
    """The analytics→vol model-serving path: fit centrally, serialize to JSON
    (Redis), deserialize at the edge, infer per-cycle — bit-identical output."""
    X = _three_cluster_data()
    gmm, fit = fit_gmm(X)
    assert gmm is not None and fit is not None

    # Full round-trip through JSON, exactly as it travels over Redis.
    payload = json.loads(json.dumps(serialize_gmm(gmm, fit)))
    gmm2, fit2 = deserialize_gmm(payload)

    # Metadata preserved.
    assert fit2.n_obs == fit.n_obs
    assert fit2.converged == fit.converged
    assert fit2.component_to_label == fit.component_to_label

    # infer_proba identical on points across the three regimes — no re-fit,
    # no numerical drift (precisions_cholesky_ restored verbatim).
    for x in (CALM_MEAN, PRE_EVENT_MEAN, STRESSED_MEAN, (7.0, 1.5, -1.0)):
        r1 = infer_proba(gmm, np.array(x), fit)
        r2 = infer_proba(gmm2, np.array(x), fit2)
        assert r1.label == r2.label
        assert r1.p_calm == r2.p_calm
        assert r1.p_stressed == r2.p_stressed
        assert r1.p_pre_event == r2.p_pre_event


def test_deserialize_gmm_is_marked_fitted_without_refit():
    """A restored model must predict without ever calling .fit() (no training
    data at the edge)."""
    gmm, fit = fit_gmm(_three_cluster_data())
    assert gmm is not None and fit is not None
    gmm2, _ = deserialize_gmm(serialize_gmm(gmm, fit))
    # predict_proba would raise NotFittedError if the attrs weren't restored.
    proba = gmm2.predict_proba(np.array([STRESSED_MEAN]))
    assert proba.shape == (1, 3)
    assert proba.sum() == pytest.approx(1.0, abs=1e-6)
