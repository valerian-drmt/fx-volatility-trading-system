"""Project a book's per-cell vega onto the PCA loadings (R11 G-risk).

The vol PCA is fit on the standardized 30-dim IV surface (6 tenors × 5 deltas,
tenor outer / delta inner — see ``core.vol.pca_engine``). A unit move in the
PC_k *score* corresponds to ``loadings[k] · stds`` in raw vol-points, so the
book's vega P&L sensitivity to that mode is

    vega_pc[k] = Σ_cell  vega_cell · loadings[k][cell] · stds[cell]

i.e. the dollar P&L of the book per unit-PC_k move. This is what the Risk-tab
"vega → PCA mode" card shows: how the book's vega is allocated across level /
slope / curvature.
"""
from __future__ import annotations

import numpy as np

TENORS = ("1M", "2M", "3M", "4M", "5M", "6M")
DELTAS = ("10dp", "25dp", "atm", "25dc", "10dc")
N_CELLS = len(TENORS) * len(DELTAS)  # 30
PC_NAMES = {1: "level", 2: "slope", 3: "curvature"}


def tenor_index(dte_days: float) -> int:
    """DTE (days) → 0..5 tenor bucket (≈ month, clamped to 1M..6M)."""
    return min(len(TENORS) - 1, max(0, round(dte_days / 30.0) - 1))


def delta_index(bs_delta: float) -> int:
    """Signed BS delta (put < 0, call > 0) → 0..4 grid column.

    10dp=0, 25dp=1, atm=2, 25dc=3, 10dc=4. ATM bucket = |Δ| in [0.375, 0.625];
    the 25Δ / 10Δ split sits at |Δ| = 0.175.
    """
    ad = abs(bs_delta)
    if ad >= 0.375:
        return 2  # atm
    if bs_delta < 0:
        return 1 if ad >= 0.175 else 0  # 25dp / 10dp
    return 3 if ad >= 0.175 else 4  # 25dc / 10dc


def cell_index(dte_days: float, bs_delta: float) -> int:
    """(DTE, signed delta) → 0..29 flat index (tenor outer, delta inner)."""
    return tenor_index(dte_days) * len(DELTAS) + delta_index(bs_delta)


def project_vega(
    vega_cells: list[float],
    loadings: list[list[float]],
    stds: list[float],
    n_pc: int = 3,
) -> list[float]:
    """Book vega P&L sensitivity to each of the first ``n_pc`` PCs (USD per
    unit-PC-score move). Returns ``[]`` if the dimensions are inconsistent."""
    v = np.asarray(vega_cells, dtype=float)  # (30,)
    L = np.asarray(loadings, dtype=float)  # (k, 30)
    s = np.asarray(stds, dtype=float)  # (30,)
    if v.ndim != 1 or L.ndim != 2 or v.shape[0] != L.shape[1] or s.shape != v.shape:
        return []
    vs = v * s
    k = min(n_pc, L.shape[0])
    return [float(vs @ L[i]) for i in range(k)]
