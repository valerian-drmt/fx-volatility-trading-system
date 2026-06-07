"""GMM 3-component regime classifier — Step 1 §3 zone 2.

Replaces the threshold heuristic once we have enough feature history. Pure
helpers (no DB / Redis access) — vol-engine fetches the matrix and feeds it.

Mapping components → labels (deterministic across re-fits) :
    calm       : component with the LOWEST mean vol_level
    stressed   : component with the HIGHEST mean vol_level
    pre_event  : the remaining (middle) component

Why this works in our 3-feature space (vol_level, vol_of_vol, term_slope) :
calm regimes cluster around low vol_level + low vov + flat slope ; stressed
clusters around high vol_level + high vov ; pre_event sits at moderate
vol_level with anomalous slope (steep or inverted) — naturally falling in the
middle of the vol_level distribution.

Cf. STEP1 §3 zone 2 + the `p_calm/p_stressed/p_pre_event` columns in
``regime_snapshots`` (already migrated 010, currently NULL).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Minimum obs to fit a 3-component GMM. Lower = noisy first models, higher =
# delays the activation. 50 ≈ 2.5h of vol-engine cycles (180s).
MIN_OBS_GMM: int = 50

# Random seed for sklearn's k-means init — deterministic across cycles.
GMM_RANDOM_STATE: int = 42

LABELS_3 = ("calm", "stressed", "pre_event")


@dataclass(frozen=True)
class GmmFitResult:
    n_obs: int
    component_to_label: dict[int, str]   # GMM component idx → label
    converged: bool


@dataclass(frozen=True)
class GmmInferenceResult:
    label: str                            # argmax label
    p_calm: float
    p_stressed: float
    p_pre_event: float


def fit_gmm(X: np.ndarray) -> tuple[object | None, GmmFitResult | None]:
    """Fit a 3-component GMM on ``X`` (n_obs, n_features=3).

    Returns ``(model, fit_result)`` or ``(None, None)`` if not enough obs.
    Caller is expected to keep the returned model in memory and re-use it
    for ``infer_proba`` on the live observation.
    """
    if X.ndim != 2 or X.shape[1] < 1:
        return None, None
    if X.shape[0] < MIN_OBS_GMM:
        return None, None

    # Lazy import — sklearn is heavy (~30MB) and only needed when GMM fires.
    from sklearn.mixture import GaussianMixture

    gmm = GaussianMixture(
        n_components=3,
        covariance_type="full",
        random_state=GMM_RANDOM_STATE,
        max_iter=200,
        # 0.5 = numerical-stability regularizer for the small-N regime
        # we run in (N=200-500). Has no semantic meaning ; without it the
        # 3 covariances collapse to near-singular when training data is
        # tightly clustered (calm-only periods). The model is still
        # SHADOW-ONLY at this stage (cf. STEP1 §13) — labels stay driven
        # by threshold_heuristic until the data spans real regimes.
        reg_covar=0.5,
    )
    gmm.fit(X)
    component_to_label = _map_components_to_labels(gmm.means_, vol_level_col=0)
    return gmm, GmmFitResult(
        n_obs=int(X.shape[0]),
        component_to_label=component_to_label,
        converged=bool(gmm.converged_),
    )


def infer_proba(
    gmm: object, x: np.ndarray, fit: GmmFitResult,
) -> GmmInferenceResult:
    """Project a single observation ``x`` (n_features,) on the fitted GMM.

    Returns a result dict with the predicted label + the 3 probas mapped to
    the (calm, stressed, pre_event) order.
    """
    proba = gmm.predict_proba(x.reshape(1, -1))[0]  # shape (3,)
    by_label: dict[str, float] = {}
    for comp_idx, label in fit.component_to_label.items():
        by_label[label] = float(proba[comp_idx])
    # Argmax by mapped label (not raw component idx).
    label = max(by_label.items(), key=lambda kv: kv[1])[0]
    return GmmInferenceResult(
        label=label,
        p_calm=round(by_label.get("calm", 0.0), 4),
        p_stressed=round(by_label.get("stressed", 0.0), 4),
        p_pre_event=round(by_label.get("pre_event", 0.0), 4),
    )


def _map_components_to_labels(
    means: np.ndarray, vol_level_col: int,
) -> dict[int, str]:
    """Sort components by ascending mean vol_level → calm / pre_event / stressed.

    With 3 components, the lowest vol_level mean = calm, highest = stressed,
    middle = pre_event. Stable across cycles because we sort the same way
    each fit. ``means`` shape : (n_components, n_features).
    """
    n = means.shape[0]
    if n != 3:
        # Fallback : map by ordinal — rarely happens since we ask for 3.
        return {i: LABELS_3[min(i, 2)] for i in range(n)}
    order = np.argsort(means[:, vol_level_col])  # ascending
    return {
        int(order[0]): "calm",
        int(order[1]): "pre_event",
        int(order[2]): "stressed",
    }
