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
        ``{"1M": {"sigma_model_pct": …, "garch_converged": bool}, ...}`` —
        empty dict if the fit fails (insufficient data, numerical
        divergence, etc.). ``garch_converged`` False means the optimiser did
        not converge and the number is the empirical leg alone.
    """
    if closes is None:
        return {}
    # Input validation (#312, audit 02#8): only finite, positive closes feed
    # the log returns; a NaN or a zero used to poison the whole fit.
    c = np.asarray(closes, dtype=float)
    c = c[np.isfinite(c) & (c > 0)]
    if len(c) < 5:
        return {}
    returns = np.diff(np.log(c)) * 100
    if not np.all(np.isfinite(returns)) or float(np.std(returns)) == 0.0:
        return {}
    try:
        fit = arch_model(returns, vol="Garch", p=1, q=1, mean="Constant", dist="normal").fit(
            disp="off"
        )
    except (ValueError, RuntimeError, np.linalg.LinAlgError):
        return {}
    # The optimiser's verdict is part of the answer: on a non-converged fit the
    # GARCH leg is not trusted and the projection falls back to the empirical
    # mean-reversion leg alone (blend 0), flagged on every tenor.
    converged = int(getattr(fit, "convergence_flag", 0) or 0) == 0
    omega = float(fit.params["omega"])
    alpha = float(fit.params["alpha[1]"])
    beta = float(fit.params["beta[1]"])
    persistence = min(alpha + beta, 0.9999)
    kappa = -np.log(persistence)
    cond_vol = fit.conditional_volatility
    last_cond = cond_vol[-1] if hasattr(cond_vol, "__getitem__") else float(cond_vol)
    var_c = (np.sqrt(last_cond ** 2 * 252) / 100) ** 2
    var_lr = (np.sqrt(omega / (1 - persistence) * 252) / 100) ** 2
    if not (np.isfinite(var_c) and np.isfinite(var_lr)):
        return {}
    effective_blend = blend if converged else 0.0
    rv_map = rv_map or {}
    out: dict[str, dict[str, float]] = {}
    for label, T in tenor_t.items():
        # AVERAGE variance over [0, T], the quantity an option priced to T
        # sees, not the instantaneous variance at T (#312):
        #   (1/T) * integral_0^T  var_lr + (var_c - var_lr) e^{-k t} dt
        #   = var_lr + (var_c - var_lr) (1 - e^{-kT}) / (kT)
        if kappa * T > 1e-12:
            decay = (1.0 - np.exp(-kappa * T)) / (kappa * T)
        else:
            decay = 1.0
        var_T = var_lr + (var_c - var_lr) * decay
        vol_garch = float(np.sqrt(max(var_T, 0)) * 100)
        rv_tenor = rv_map.get(label, {}).get("RV_pct") if rv_map else None
        if rv_tenor is not None and rv_full is not None:
            vol_empirical = rv_full + (rv_tenor - rv_full) * np.exp(-emp_kappa * T)
        else:
            vol_empirical = vol_garch
        vol_model = effective_blend * vol_garch + (1 - effective_blend) * vol_empirical
        out[label] = {"sigma_model_pct": round(float(vol_model), 4), "garch_converged": converged}
    return out
