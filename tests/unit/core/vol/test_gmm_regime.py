from __future__ import annotations

import numpy as np
import pytest

from core.vol.gmm_regime import MIN_OBS_GMM, GmmFitResult, fit_gmm, infer_proba


def _synthetic_three_regimes(per_cluster: int = 30) -> np.ndarray:
    """3 well-separated clusters in (vol_level, vol_of_vol, term_slope) space."""
    rng = np.random.default_rng(0)
    calm = rng.normal([5.0, 0.1, 0.0], 0.3, size=(per_cluster, 3))
    pre_event = rng.normal([8.0, 0.6, 0.2], 0.3, size=(per_cluster, 3))
    stressed = rng.normal([14.0, 1.5, -0.5], 0.3, size=(per_cluster, 3))
    return np.vstack([calm, pre_event, stressed])


@pytest.mark.parametrize("n_obs", [10, MIN_OBS_GMM - 1])
def test_fit_gmm_returns_none_below_min_obs(n_obs: int) -> None:
    assert fit_gmm(np.zeros((n_obs, 3))) == (None, None)


def test_fit_gmm_rejects_non_2d() -> None:
    assert fit_gmm(np.zeros(MIN_OBS_GMM)) == (None, None)


def test_fit_gmm_maps_components_to_three_regimes() -> None:
    model, fit = fit_gmm(_synthetic_three_regimes())
    assert model is not None
    assert isinstance(fit, GmmFitResult)
    assert fit.n_obs == 90
    assert fit.converged
    assert set(fit.component_to_label.values()) == {"calm", "pre_event", "stressed"}


def test_infer_proba_assigns_high_vol_point_to_stressed() -> None:
    model, fit = fit_gmm(_synthetic_three_regimes())
    res = infer_proba(model, np.array([14.0, 1.5, -0.5]), fit)
    assert res.label == "stressed"
    assert res.p_calm + res.p_stressed + res.p_pre_event == pytest.approx(1.0, abs=1e-6)
    assert res.p_stressed > res.p_calm
