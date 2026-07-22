"""HAR-RV — Heterogeneous Autoregressive Realised Volatility (Corsi 2009).

HAR-RV beats GARCH(1,1) on mixed-horizon realised vol forecasting because
it explicitly encodes three time-scales of vol persistence (daily,
weekly, monthly) that match how different market participants trade :

    RV_{t+1} = β0 + β_d · RV_t + β_w · RV_t^(w) + β_m · RV_t^(m) + ε

where :
    RV_t^(w) = mean(RV_{t-4..t})          (past week, 5 trading days)
    RV_t^(m) = mean(RV_{t-21..t})         (past month, 22 trading days)

Fit : plain OLS on log-RV (variance stabilising, keeps positivity).
Project at horizon h days by iterating the 1-step-ahead model and taking
the mean of daily forecasts over the horizon — the standard direct
approach in Corsi's applied work, valid when the model is stationary
(β_d + β_w + β_m < 1).

Returned value is **annualised σ percent** (%-scaled, e.g. 6.5), to
match the GARCH output convention used throughout the engine.

This is a *physical-measure* (P) estimator. It does NOT include a
risk-premium — see ``core/vol/vrp.py::q_measure_from_p`` for the
Q-measure conversion the pricing layer should use.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

WEEKLY_LAG = 5
MONTHLY_LAG = 22
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class HarCoef:
    beta0: float
    beta_d: float
    beta_w: float
    beta_m: float
    sigma_residual: float  # std of OLS residuals on log-RV (in log-% space)


def _daily_rv_percent_from_closes(closes: np.ndarray) -> np.ndarray:
    """Squared-log-return daily RV, annualised % (close-to-close).

    HAR traditionally feeds on intraday RV ; with only daily bars we use
    |r_t| × √252 as the daily σ percent proxy. Same convention as the
    Yang-Zhang output, so regressors and target are in the same unit.
    """
    log_returns = np.diff(np.log(closes))
    daily_sigma_pct = np.abs(log_returns) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
    # Floor at a small positive value so log() is safe.
    return np.maximum(daily_sigma_pct, 1e-4)


def _build_features(rv_series: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Stack (RV_d, RV_w, RV_m) regressors aligned with RV_{t+1} targets."""
    n = len(rv_series)
    if n < MONTHLY_LAG + 2:
        return np.empty((0, 3)), np.empty(0)
    rows, targets = [], []
    for t in range(MONTHLY_LAG, n - 1):
        rv_d = rv_series[t]
        rv_w = rv_series[t - WEEKLY_LAG + 1 : t + 1].mean()
        rv_m = rv_series[t - MONTHLY_LAG + 1 : t + 1].mean()
        rows.append([rv_d, rv_w, rv_m])
        targets.append(rv_series[t + 1])
    return np.asarray(rows), np.asarray(targets)


def fit_har_rv(closes: np.ndarray | list[float]) -> HarCoef | None:
    """OLS fit of HAR(d/w/m) on log-RV. Returns None if the series is too short."""
    c = np.asarray(closes, dtype=float)
    c = c[np.isfinite(c) & (c > 0)]
    if len(c) < MONTHLY_LAG + 20:  # need at least ~20 data points after lags.
        return None
    rv = _daily_rv_percent_from_closes(c)
    X, y = _build_features(rv)
    if len(y) < 10:
        return None
    # Work in log-space for positivity.
    X_log = np.log(X)
    y_log = np.log(y)
    design = np.column_stack([np.ones(len(X_log)), X_log])
    try:
        coef, *_ = np.linalg.lstsq(design, y_log, rcond=None)
    except np.linalg.LinAlgError:
        return None
    residuals = y_log - design @ coef
    sigma_resid = float(residuals.std(ddof=len(coef)))
    return HarCoef(
        beta0=float(coef[0]),
        beta_d=float(coef[1]),
        beta_w=float(coef[2]),
        beta_m=float(coef[3]),
        sigma_residual=sigma_resid,
    )


def project_horizon(coef: HarCoef, rv_series: np.ndarray, horizon_days: int) -> float:
    """Iterate the 1-step-ahead HAR forecast, return mean σ over the horizon.

    Running the model in log-space keeps RV positive. The returned value
    is the annualised σ percent at horizon ``horizon_days`` — the simple
    average of the daily σ forecasts, which is what the option pricer
    needs for a fair vol at that maturity.
    """
    if horizon_days <= 0:
        return float("nan")
    rv = list(rv_series[-max(MONTHLY_LAG, len(rv_series)) :])
    daily_forecasts: list[float] = []
    for _ in range(horizon_days):
        rv_d = math.log(max(rv[-1], 1e-4))
        rv_w = math.log(max(np.mean(rv[-WEEKLY_LAG:]), 1e-4))
        rv_m = math.log(max(np.mean(rv[-MONTHLY_LAG:]), 1e-4))
        log_next = coef.beta0 + coef.beta_d * rv_d + coef.beta_w * rv_w + coef.beta_m * rv_m
        next_rv = math.exp(log_next)
        daily_forecasts.append(next_rv)
        rv.append(next_rv)
    return float(np.mean(daily_forecasts))


def fit_and_project_har(
    closes: Any, tenor_days: dict[str, int]
) -> dict[str, dict[str, float]]:
    """Return ``{tenor: {\"sigma_har_pct\": x}}`` — one fair σ per tenor (P-measure).

    ``closes`` can be a pandas Series / DataFrame column or a raw ndarray.
    ``tenor_days`` maps tenor labels to number of *calendar* days, which we
    convert to trading days via 5/7. Empty dict if the fit fails.
    """
    if hasattr(closes, "to_numpy"):
        arr = closes.to_numpy()
    else:
        arr = np.asarray(closes, dtype=float)
    coef = fit_har_rv(arr)
    if coef is None:
        return {}
    rv = _daily_rv_percent_from_closes(np.asarray(arr, dtype=float))
    out: dict[str, dict[str, float]] = {}
    for label, days in tenor_days.items():
        trading_days = max(1, round(days * 5 / 7))
        sigma_pct = project_horizon(coef, rv, trading_days)
        out[label] = {"sigma_har_pct": round(sigma_pct, 4)}
    return out
