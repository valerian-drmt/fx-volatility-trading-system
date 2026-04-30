"""Step 2 — PCA factor model helpers.

Pure functions only : numpy in / numpy out. No DB / Redis / sklearn — we
do PCA via ``numpy.linalg.svd`` to avoid pulling sklearn into the
vol-engine image.

Pipeline (cf. STEP2_SIGNAL_DETECTION.md §7) :

    fit_pca_svd(X)         → loadings, eigenvalues, var_ratio, means, stds
    sign_correct_loadings  → flip eigenvectors to preserve temporal sign
    project(x, model)      → raw_scores per PC (dim n_components)
    zscore_against(...)    → standardise raw_score vs hist projections
    classify_label         → CHEAP / FAIR / EXPENSIVE
    actionable_check       → 5 gates (variance, stability, magnitude,
                                       persistence, coherence)
    check_coherence        → cross-PC contradictions
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

# 30-dim canonical grid : 6 tenors × 5 deltas (rows = tenor outer, cols = delta inner).
TENORS = ("1M", "2M", "3M", "4M", "5M", "6M")
DELTAS = ("10dp", "25dp", "atm", "25dc", "10dc")
N_FEATURES = len(TENORS) * len(DELTAS)  # 30

MIN_VARIANCE_EXPLAINED = {1: 0.60, 2: 0.15, 3: 0.05}
THRESHOLDS = {"weak": 1.0, "moderate": 1.5, "strong": 2.0, "extreme": 3.0}


@dataclass(frozen=True)
class PcaFitResult:
    means: np.ndarray              # (30,)
    stds: np.ndarray               # (30,)
    loadings: np.ndarray           # (n_components, 30)
    eigenvalues: np.ndarray        # (n_components,)
    variance_explained_ratio: np.ndarray  # (n_components,)
    n_obs_used: int


@dataclass(frozen=True)
class ActionableFlag:
    actionable: bool
    reason: str | None
    strength: str | None  # weak | moderate | strong | extreme | None


def feature_vector_from_surface(surface: dict) -> np.ndarray | None:
    """Extract the 30-dim IV vector (in %) from a surface dict.

    Returns None if any IV is missing — callers must skip the cycle.
    """
    out: list[float] = []
    for tenor in TENORS:
        node = surface.get(tenor)
        if not isinstance(node, dict):
            return None
        for delta in DELTAS:
            d = node.get(delta)
            if not isinstance(d, dict):
                return None
            iv = d.get("iv")
            if not isinstance(iv, (int, float)):
                return None
            out.append(float(iv) * 100.0)
    return np.asarray(out, dtype=float)


def fit_pca_svd(X: np.ndarray, n_components: int = 6) -> PcaFitResult:
    """Fit PCA via SVD on the standardised matrix.

    Deterministic (numpy.linalg.svd is deterministic). Returns top
    ``n_components`` PCs sorted by descending variance.
    """
    if X.ndim != 2 or X.shape[1] != N_FEATURES:
        raise ValueError(f"X must be (T, {N_FEATURES}), got {X.shape}")
    if X.shape[0] < n_components + 1:
        raise ValueError(f"Need >= {n_components+1} obs, got {X.shape[0]}")

    means = X.mean(axis=0)
    stds = X.std(axis=0, ddof=1)
    stds = np.where(stds <= 0, 1.0, stds)  # avoid div-by-zero on constant columns
    X_std = (X - means) / stds

    # Truncated SVD : X_std ~= U * diag(S) * Vt
    _, S, Vt = np.linalg.svd(X_std, full_matrices=False)
    loadings = Vt[:n_components]
    eigenvalues = (S[:n_components] ** 2) / max(X.shape[0] - 1, 1)
    total_var = (S ** 2).sum() / max(X.shape[0] - 1, 1)
    var_ratio = eigenvalues / total_var if total_var > 0 else np.zeros_like(eigenvalues)

    return PcaFitResult(
        means=means, stds=stds, loadings=loadings,
        eigenvalues=eigenvalues, variance_explained_ratio=var_ratio,
        n_obs_used=int(X.shape[0]),
    )


def sign_correct_loadings(
    new: np.ndarray, reference: np.ndarray | None,
) -> tuple[np.ndarray, list[float], list[bool]]:
    """Flip eigenvectors of ``new`` to maximise cosine sim vs ``reference``.

    Returns ``(corrected, cosine_sims, sign_flips)``. If reference is None
    (first fit), ``cosine_sims`` is filled with NaN and no flips applied.
    """
    if reference is None:
        return new.copy(), [float("nan")] * new.shape[0], [False] * new.shape[0]

    n = min(new.shape[0], reference.shape[0])
    corrected = new.copy()
    cos_sims: list[float] = []
    flips: list[bool] = []
    for i in range(n):
        a = new[i]
        b = reference[i]
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        cos = float(np.dot(a, b) / denom) if denom > 0 else 0.0
        flipped = cos < 0
        if flipped:
            corrected[i] = -a
            cos = -cos
        cos_sims.append(cos)
        flips.append(flipped)
    for _ in range(new.shape[0] - n):
        cos_sims.append(float("nan"))
        flips.append(False)
    return corrected, cos_sims, flips


def project(x: np.ndarray, means: np.ndarray, stds: np.ndarray, loadings: np.ndarray) -> np.ndarray:
    """Project a single 30-dim observation on PC loadings → raw scores per PC."""
    x_std = (x - means) / stds
    return loadings @ x_std  # shape (n_components,)


def zscore_against(value: float, hist: Iterable[float]) -> float:
    """Standardise a value against a history of past raw scores."""
    arr = np.asarray([h for h in hist if h is not None and np.isfinite(h)], dtype=float)
    if arr.size < 5:
        return 0.0
    sigma = float(arr.std(ddof=1))
    if sigma <= 0:
        return 0.0
    return float((value - float(arr.mean())) / sigma)


def classify_label(z: float, threshold: float = THRESHOLDS["moderate"]) -> str:
    """Map a z-score to {CHEAP, FAIR, EXPENSIVE}. Sign convention :
    z > 0 = surface above expectation = EXPENSIVE.
    """
    if abs(z) < threshold:
        return "FAIR"
    return "EXPENSIVE" if z > 0 else "CHEAP"


def classify_strength(abs_z: float) -> str | None:
    if abs_z >= THRESHOLDS["extreme"]:
        return "extreme"
    if abs_z >= THRESHOLDS["strong"]:
        return "strong"
    if abs_z >= THRESHOLDS["moderate"]:
        return "moderate"
    if abs_z >= THRESHOLDS["weak"]:
        return "weak"
    return None


def actionable_check(
    *,
    pc_id: int, z_score: float, label: str,
    loadings_stable: bool, variance_explained: float,
    persistent: bool,
) -> ActionableFlag:
    """5 gates from STEP2 §2."""
    if not loadings_stable:
        return ActionableFlag(False, f"loadings_unstable_pc{pc_id}", None)
    min_var = MIN_VARIANCE_EXPLAINED.get(pc_id, 0.0)
    if variance_explained < min_var:
        return ActionableFlag(False, f"low_variance_pc{pc_id}", None)
    if abs(z_score) < THRESHOLDS["weak"]:
        return ActionableFlag(False, "signal_below_threshold", None)
    if label == "FAIR":
        return ActionableFlag(False, "label_fair", None)
    if not persistent:
        return ActionableFlag(False, "signal_not_persistent", None)
    return ActionableFlag(True, None, classify_strength(abs(z_score)))


def check_coherence(signals: dict[str, dict]) -> dict:
    """PC1/PC2 disagreement on direction → contradiction (informational, not blocking)."""
    pc1 = signals.get("pc1") or {}
    pc2 = signals.get("pc2") or {}
    contras: list[tuple[str, str]] = []
    if pc1.get("label") and pc2.get("label"):
        a, b = pc1["label"], pc2["label"]
        if {a, b} == {"CHEAP", "EXPENSIVE"}:
            contras.append(("pc1", "pc2"))
    return {"all_coherent": not contras, "contradictions": contras}


def is_persistent(z_history: list[float], threshold: float = THRESHOLDS["weak"], n_cycles: int = 3) -> bool:
    """Last ``n_cycles`` (most recent first) all have |z| > threshold AND same sign."""
    if len(z_history) < n_cycles:
        return False
    recent = z_history[:n_cycles]
    if any(abs(z) < threshold for z in recent):
        return False
    signs = {1 if z > 0 else -1 for z in recent}
    return len(signs) == 1
