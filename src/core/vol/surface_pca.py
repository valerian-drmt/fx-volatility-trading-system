"""PCA on the historical IV surface — level / slope / smile factors.

Refactor plan P3.1 + P3.2. Reduces a vol surface snapshot to 3 scalar
factors that explain the bulk of the cross-sectional variance, so the
operator can trade "vol level", "term slope" and "smile shape" as
three mutually orthogonal signals instead of six correlated tenor-wise
signals.

Surface vector
--------------
30 components : 6 tenors (1M..6M) × 5 pillars (10P, 25P, ATM, 25C, 10C).
For each pillar we use the **implied vol in percent** (e.g. 6.05 for
6.05%), which keeps the scale homogeneous across tenors.

API
---
- ``fit_pca(surfaces, n_components=3)`` : OLS PCA fit on a history of
  flattened surfaces, returns a ``PcaModel`` with loadings + explained
  variance per component. Bootstraps gracefully when the history is
  shorter than ``MIN_SAMPLES_FOR_SIGNAL`` — the model is returned but
  ``bootstrap=True`` so callers can render "accumulating".
- ``project_surface(surface_dict, model)`` : project a single snapshot
  onto the PC basis. Missing cells are imputed as the PCA mean (neutral
  impact, pulls the score toward 0).
- ``compute_pc_signals(current_scores, historical_scores, min_history)``
  : z-score per PC vs a rolling history of past scores. Returns
  ``bootstrap=True`` when insufficient, z=0 otherwise.
- ``label_pc(model)`` : heuristic naming "level" / "term_slope" /
  "smile" / "other" based on the loadings signature, matches what the
  dashboard shows.

Why not scikit-learn
--------------------
To keep the core layer dependency-light, the PCA is implemented via
``numpy.linalg.svd`` — mathematically identical to sklearn's PCA for
this size of matrix and avoids another dep in the container.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Ordered tenor labels the PCA expects on the feature axis.
DEFAULT_TENORS: tuple[str, ...] = ("1M", "2M", "3M", "4M", "5M", "6M")
# Ordered pillar labels per tenor.
DEFAULT_PILLARS: tuple[str, ...] = ("10dp", "25dp", "atm", "25dc", "10dc")
N_FEATURES = len(DEFAULT_TENORS) * len(DEFAULT_PILLARS)  # 30

# Below this many observations the loadings are too noisy to trust —
# fit still runs but the ``bootstrap`` flag warns consumers.
MIN_SAMPLES_FOR_SIGNAL: int = 50


@dataclass(frozen=True)
class PcaModel:
    mean: np.ndarray                 # (N_FEATURES,) — training mean per cell
    components: np.ndarray           # (n_components, N_FEATURES) — loadings
    explained_variance_ratio: np.ndarray  # (n_components,)
    n_samples_trained: int
    bootstrap: bool                  # True if n_samples_trained < MIN_SAMPLES_FOR_SIGNAL
    tenors: tuple[str, ...]
    pillars: tuple[str, ...]


def flatten_surface(
    surface: dict[str, Any],
    tenors: tuple[str, ...] = DEFAULT_TENORS,
    pillars: tuple[str, ...] = DEFAULT_PILLARS,
) -> np.ndarray | None:
    """Build a 30-D vector of IV percent from one engine surface dict.

    Layout : tenor-major, i.e. [1M.10P, 1M.25P, 1M.ATM, 1M.25C, 1M.10C,
    2M.10P, ..., 6M.10C]. Cells with missing ``iv`` are filled with
    np.nan — the caller is expected to impute (usually with the PCA
    mean) before the projection.
    """
    vec = np.full(len(tenors) * len(pillars), np.nan, dtype=float)
    for ti, tenor in enumerate(tenors):
        pillar_group = surface.get(tenor)
        if not isinstance(pillar_group, dict):
            continue
        for pi, pillar in enumerate(pillars):
            node = pillar_group.get(pillar)
            if not isinstance(node, dict):
                continue
            iv = node.get("iv")
            if isinstance(iv, (int, float)):
                vec[ti * len(pillars) + pi] = float(iv) * 100.0
    return vec


def fit_pca(
    surfaces: list[dict[str, Any]] | list[np.ndarray],
    n_components: int = 3,
    tenors: tuple[str, ...] = DEFAULT_TENORS,
    pillars: tuple[str, ...] = DEFAULT_PILLARS,
) -> PcaModel | None:
    """Fit PCA on a history of surfaces (dicts or pre-flattened vectors).

    Returns ``None`` if ``surfaces`` is empty or every row is nan after
    flattening. A non-None model with ``bootstrap=True`` means the fit
    ran but the sample count is below ``MIN_SAMPLES_FOR_SIGNAL`` and
    callers should either wait or render a bootstrap flag.
    """
    if not surfaces:
        return None
    rows = []
    for s in surfaces:
        if isinstance(s, np.ndarray):
            rows.append(s)
        else:
            v = flatten_surface(s, tenors=tenors, pillars=pillars)
            if v is not None:
                rows.append(v)
    X = np.asarray(rows, dtype=float)
    if X.ndim != 2 or X.size == 0:
        return None
    # Column-wise mean-imputation so every feature has a value.
    col_mean = np.nanmean(X, axis=0)
    nan_mask = np.isnan(X)
    X = np.where(nan_mask, col_mean, X)
    # Drop rows that were entirely nan (col_mean may still be nan for a
    # fully-empty column ; skip those columns by setting to 0 variance).
    col_mean = np.where(np.isnan(col_mean), 0.0, col_mean)
    X = np.where(np.isnan(X), col_mean, X)
    # Centre and SVD — classic PCA.
    Xc = X - col_mean
    n, p = Xc.shape
    if n < 2:
        return None
    k = min(n_components, n, p)
    U, s, Vt = np.linalg.svd(Xc, full_matrices=False)
    components = Vt[:k]
    explained_variance = (s[:k] ** 2) / max(n - 1, 1)
    total_variance = float(np.sum(s**2) / max(n - 1, 1))
    ratio = (
        explained_variance / total_variance
        if total_variance > 1e-12
        else np.zeros_like(explained_variance)
    )
    bootstrap = n < MIN_SAMPLES_FOR_SIGNAL
    return PcaModel(
        mean=col_mean,
        components=components,
        explained_variance_ratio=ratio,
        n_samples_trained=int(n),
        bootstrap=bool(bootstrap),
        tenors=tenors,
        pillars=pillars,
    )


def project_surface(
    surface: dict[str, Any] | np.ndarray,
    model: PcaModel,
) -> np.ndarray:
    """Return ``(n_components,)`` PC scores for one surface snapshot.

    Missing cells are imputed with the training mean → zero contribution
    to the score on that axis.
    """
    if isinstance(surface, np.ndarray):
        vec = surface.astype(float, copy=True)
    else:
        vec = flatten_surface(surface, tenors=model.tenors, pillars=model.pillars)
    if vec is None:
        return np.zeros(model.components.shape[0], dtype=float)
    vec = np.where(np.isnan(vec), model.mean, vec)
    return model.components @ (vec - model.mean)


@dataclass(frozen=True)
class PcSignal:
    pc: int                 # 1, 2, 3 ...
    label: str              # "level" | "term_slope" | "smile" | "other"
    current: float
    mean: float
    std: float
    z: float
    bootstrap: bool


def compute_pc_signals(
    current_scores: np.ndarray,
    historical_scores: list[np.ndarray] | np.ndarray,
    model: PcaModel,
    min_history: int = MIN_SAMPLES_FOR_SIGNAL,
) -> list[PcSignal]:
    """Z-score each PC of ``current_scores`` against the history.

    With fewer than ``min_history`` snapshots — or the model itself in
    bootstrap — the signals are flagged ``bootstrap=True`` with z=0.
    """
    H = np.asarray(historical_scores, dtype=float) if len(historical_scores) else np.empty((0, 0))
    n_hist = H.shape[0]
    bootstrap = model.bootstrap or n_hist < min_history
    labels = label_pc(model)
    out: list[PcSignal] = []
    for i, current in enumerate(current_scores):
        if H.size == 0 or H.ndim < 2 or i >= H.shape[1]:
            mean_, std_ = 0.0, 0.0
        else:
            mean_ = float(np.mean(H[:, i]))
            std_ = float(np.std(H[:, i], ddof=1)) if n_hist >= 2 else 0.0
        z = 0.0 if bootstrap or std_ <= 1e-9 else float((current - mean_) / std_)
        out.append(PcSignal(
            pc=i + 1, label=labels[i] if i < len(labels) else "other",
            current=float(current), mean=mean_, std=std_, z=z,
            bootstrap=bool(bootstrap),
        ))
    return out


def label_pc(model: PcaModel) -> list[str]:
    """Heuristic naming of the first three PCs from the loadings signature.

    - **level** : all loadings have the same sign and similar magnitude
      on every tenor-pillar cell.
    - **term_slope** : loadings monotone across tenors (short vs long).
    - **smile** : loadings alternate sign across pillars (ATM vs wings).
    - **other** : anything else, labelled ``pcN`` for debugging.
    """
    if model.components.size == 0:
        return []
    names: list[str] = []
    assigned = {"level", "term_slope", "smile"}
    for i in range(model.components.shape[0]):
        loading = model.components[i]
        name = _classify_loading(loading, model.tenors, model.pillars)
        # Ensure uniqueness (avoid two PCs both labelled "level").
        if name in names or name not in assigned:
            name = f"pc{i + 1}"
        else:
            assigned.discard(name)
        names.append(name)
    return names


def _classify_loading(
    loading: np.ndarray, tenors: tuple[str, ...], pillars: tuple[str, ...],
) -> str:
    n_t, n_p = len(tenors), len(pillars)
    matrix = loading.reshape(n_t, n_p)
    # Same-sign everywhere → level.
    signs = np.sign(matrix[matrix != 0])
    if signs.size > 0 and np.all(signs == signs[0]):
        return "level"
    # Monotone across tenors on the ATM column → term_slope.
    atm_col = matrix[:, n_p // 2]
    diffs = np.diff(atm_col)
    if np.all(diffs > 0) or np.all(diffs < 0):
        return "term_slope"
    # Alternating sign across pillars on the middle tenor → smile.
    middle_row = matrix[n_t // 2]
    wings = middle_row[0] + middle_row[-1]
    atm_val = middle_row[n_p // 2]
    if atm_val * wings < 0:
        return "smile"
    return "other"
