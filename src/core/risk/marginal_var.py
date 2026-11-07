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

from core.risk.var_stats import diversification_pct, finite, loss_at

MIN_DAYS = 5  # below this the historical decomposition is not meaningful


def component_var(series_by_id: dict[str, list[float]], conf: float = 0.99) -> dict:
    """Decompose portfolio VaR into per-position standalone + component VaR.

    ``series_by_id`` maps an opaque id → that position's daily P&L delta series
    (USD). Series are right-aligned to their common minimum length. Returns
    ``positions: []`` (with ``n_days``) when there is too little history.
    """
    # A series with a non-finite value is dropped as a whole (#316): the
    # alignment below needs every position on the same scenario grid, and a
    # NaN in one position would poison the portfolio vector. ``dropped`` says
    # which ones; the caller forwards it.
    clean: dict[str, list[float]] = {}
    dropped: list[dict] = []
    for k, v in series_by_id.items():
        if not v:
            continue
        f = finite(v)
        if len(f) != len(v):
            dropped.append({"id": k, "reason": "non_finite_pnl"})
            continue
        clean[k] = f
    ids = list(clean)
    if not ids:
        return {"portfolio_var_usd": 0.0, "diversification_pct": 0.0, "n_days": 0, "positions": [], "dropped": dropped}
    n = min(len(clean[k]) for k in ids)
    if n < MIN_DAYS:
        return {"portfolio_var_usd": 0.0, "diversification_pct": 0.0, "n_days": n, "positions": [], "dropped": dropped}

    mat = np.array([clean[k][-n:] for k in ids], dtype=float).T  # (n_days, n_pos)
    pf = mat.sum(axis=1)
    var_p = loss_at(pf, conf)
    var_pf = float(np.var(pf, ddof=1)) or 1.0

    positions = []
    standalone_sum = 0.0
    for i, k in enumerate(ids):
        standalone = loss_at(mat[:, i], conf)
        standalone_sum += standalone
        cov_i = float(np.cov(mat[:, i], pf, ddof=1)[0, 1])
        comp = var_p * cov_i / var_pf
        positions.append({
            "id": k,
            "standalone_usd": round(standalone, 2),
            "component_usd": round(comp, 2),
            "pct": round(100.0 * comp / var_p, 1) if var_p else 0.0,
        })
    return {
        "portfolio_var_usd": round(var_p, 2),
        "diversification_pct": round(diversification_pct(var_p, standalone_sum), 1),
        "n_days": n,
        "positions": positions,
        "dropped": dropped,
    }
