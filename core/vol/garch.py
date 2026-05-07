"""GARCH(1,1) term-structure projection blended with empirical mean-reversion.

The IB OHLC fetch stays in the caller (engine / service) — once the close
prices are in memory, this module owns the fit + projection + blend with
RV mean-reversion. Returns a dict keyed by tenor label with
``sigma_model_pct`` (annualised %, identical convention to the live
vol-engine publication).
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from arch import arch_model


def fit_and_project_garch(
    closes: np.ndarray,
    tenor_t: Mapping[str, float],
    rv_map: Mapping[str, Mapping[str, float]] | None = None,
    rv_full: float | None = None,
    blend: float = 0.50,
    emp_kappa: float = 2.0,
) -> dict[str, dict[str, float]]:
    """Fit GARCH(1,1) on ``closes`` and project a vol term-structure.

    Args:
        closes: 1-D array of adjusted-close prices.
        tenor_t: {label -> year fraction}.
        rv_map: optional {label -> {"RV_pct": …}} from Yang-Zhang, used for
            the empirical mean-reversion leg.
        rv_full: full-sample RV % (acts as the empirical long-run anchor).
        blend: weight assigned to the GARCH projection vs. the empirical
            leg. ``blend=1`` = pure GARCH, ``blend=0`` = pure empirical.
        emp_kappa: mean-reversion speed for the empirical leg.

    Returns:
        ``{"1M": {"sigma_model_pct": …}, ...}`` — empty dict if the fit
        fails (insufficient data, numerical divergence, etc.).
    """
    if closes is None or len(closes) < 5:
        return {}

    returns = np.diff(np.log(closes)) * 100
    try:
        fit = arch_model(returns, vol="Garch", p=1, q=1, mean="Constant", dist="normal").fit(
            disp="off"
        )
    except (ValueError, RuntimeError, np.linalg.LinAlgError):
        return {}

    omega = fit.params["omega"]
    alpha = fit.params["alpha[1]"]
    beta = fit.params["beta[1]"]
    persistence = min(alpha + beta, 0.9999)
    kappa = -np.log(persistence)

    cond_vol = fit.conditional_volatility
    last_cond = cond_vol[-1] if hasattr(cond_vol, "__getitem__") else float(cond_vol)
    var_c = (np.sqrt(last_cond ** 2 * 252) / 100) ** 2
    var_lr = (np.sqrt(omega / (1 - persistence) * 252) / 100) ** 2

    rv_map = rv_map or {}
    out: dict[str, dict[str, float]] = {}
    for label, T in tenor_t.items():
        var_T = var_lr + (var_c - var_lr) * np.exp(-kappa * T)
        vol_garch = float(np.sqrt(max(var_T, 0)) * 100)

        rv_tenor = rv_map.get(label, {}).get("RV_pct") if rv_map else None
        if rv_tenor is not None and rv_full is not None:
            vol_empirical = rv_full + (rv_tenor - rv_full) * np.exp(-emp_kappa * T)
        else:
            vol_empirical = vol_garch

        vol_model = blend * vol_garch + (1 - blend) * vol_empirical
        out[label] = {"sigma_model_pct": round(vol_model, 4)}

    return out
