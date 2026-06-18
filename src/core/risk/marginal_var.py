"""Component (marginal) VaR decomposition (R11 G-risk).

Given each open position's aligned daily P&L series, the portfolio P&L is their
sum. Historical VaR_p is the loss quantile of the portfolio series. Each
position's **component VaR** (Euler allocation) is

    comp_i = VaR_p · cov(pnl_i, pnl_p) / var(pnl_p)

which sums to VaR_p because Σ_i cov(pnl_i, pnl_p) = var(pnl_p). **Standalone VaR_i**
is the position's own loss quantile; **diversification** = 1 − VaR_p / Σ standalone
(how much the correlation structure shrinks the total vs. summing positions naively).
"""
from __future__ import annotations

import numpy as np

MIN_DAYS = 5  # below this the historical decomposition is not meaningful


def _loss_var(series: np.ndarray, conf: float) -> float:
    """Historical VaR as a positive USD loss at the given confidence."""
    if series.size == 0:
        return 0.0
    q = float(np.percentile(series, (1.0 - conf) * 100.0))
    return max(0.0, -q)


def component_var(series_by_id: dict[str, list[float]], conf: float = 0.99) -> dict:
    """Decompose portfolio VaR into per-position standalone + component VaR.

    ``series_by_id`` maps an opaque id → that position's daily P&L delta series
    (USD). Series are right-aligned to their common minimum length. Returns
    ``positions: []`` (with ``n_days``) when there is too little history.
    """
    ids = [k for k, v in series_by_id.items() if v]
    if not ids:
        return {"portfolio_var_usd": 0.0, "diversification_pct": 0.0, "n_days": 0, "positions": []}
    n = min(len(series_by_id[k]) for k in ids)
    if n < MIN_DAYS:
        return {"portfolio_var_usd": 0.0, "diversification_pct": 0.0, "n_days": n, "positions": []}

    mat = np.array([series_by_id[k][-n:] for k in ids], dtype=float).T  # (n_days, n_pos)
    pf = mat.sum(axis=1)
    var_p = _loss_var(pf, conf)
    var_pf = float(np.var(pf, ddof=1)) or 1.0

    positions = []
    standalone_sum = 0.0
    for i, k in enumerate(ids):
        standalone = _loss_var(mat[:, i], conf)
        standalone_sum += standalone
        cov_i = float(np.cov(mat[:, i], pf, ddof=1)[0, 1])
        comp = var_p * cov_i / var_pf
        positions.append({
            "id": k,
            "standalone_usd": round(standalone, 2),
            "component_usd": round(comp, 2),
            "pct": round(100.0 * comp / var_p, 1) if var_p else 0.0,
        })
    diversification = round(100.0 * (1.0 - var_p / standalone_sum), 1) if standalone_sum else 0.0
    return {
        "portfolio_var_usd": round(var_p, 2),
        "diversification_pct": diversification,
        "n_days": n,
        "positions": positions,
    }
