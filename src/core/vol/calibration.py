"""Walk-forward calibration helpers (Phase P4).

Two calibrators, both bootstrap-safe :

- ``calibrate_w1_closed_form(anchors, garch_forecasts, realised)``
  closed-form least-squares for the W₁ coefficient in the convex
  combination σ_fair = W₁·Anchor + (1−W₁)·GARCH :

      W₁* = Σ (A_t − G_t)(R_{t+τ} − G_t) / Σ (A_t − G_t)²

  Clipped to [0, 1]. Returns ``None`` below ``MIN_OBSERVATIONS_W1``
  so the caller falls back to the default (0.65 from the monolith).

- ``calibrate_vrp_empirical(vrp_realised_by_tenor)`` converts a rolling
  list of ex-post VRP observations into per-tenor (mean, std)
  estimates, ready to override the literature defaults in
  ``core/vol/vrp.py::VRP_DEFAULTS_VOL_PTS``. Below
  ``MIN_OBSERVATIONS_VRP`` per tenor the fallback default is returned.

Both return structures include a ``bootstrap`` flag so the dashboard
can surface "calibration learning" rather than believing noise.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Below these thresholds the calibrator refuses to emit a calibrated
# value and the caller falls back to the literature default.
MIN_OBSERVATIONS_W1: int = 30        # ~1 month daily
MIN_OBSERVATIONS_VRP_PER_TENOR: int = 60   # ~3 months


@dataclass(frozen=True)
class W1Estimate:
    value: float
    n_samples: int
    bootstrap: bool
    source: str          # 'empirical' or 'default'


@dataclass(frozen=True)
class VrpStats:
    tenor: str
    mean: float
    std: float
    n_samples: int
    bootstrap: bool
    source: str


def calibrate_w1_closed_form(
    anchors: list[float],
    garch_forecasts: list[float],
    realised: list[float],
    default: float = 0.65,
) -> W1Estimate:
    """Closed-form W₁ that minimises squared error of the convex blend.

    Every input is a list aligned on the same timestamps : ``anchors[t]``
    is the RV-anchor forecast at t, ``garch_forecasts[t]`` is the GARCH
    forecast at t, ``realised[t+τ]`` is the realised vol at the forward
    horizon. Below ``MIN_OBSERVATIONS_W1`` returns the ``default``
    (sourced from the monolith W₁=0.65 after year-2024 calibration).
    """
    if len(anchors) != len(garch_forecasts) or len(garch_forecasts) != len(realised):
        return W1Estimate(value=default, n_samples=0, bootstrap=True, source="default")
    if len(anchors) < MIN_OBSERVATIONS_W1:
        return W1Estimate(
            value=default, n_samples=len(anchors), bootstrap=True, source="default",
        )
    num = 0.0
    den = 0.0
    for a, g, r in zip(anchors, garch_forecasts, realised, strict=True):
        spread = a - g
        num += spread * (r - g)
        den += spread * spread
    if den <= 1e-12:
        return W1Estimate(
            value=default, n_samples=len(anchors), bootstrap=True, source="default",
        )
    w1 = max(0.0, min(1.0, num / den))
    return W1Estimate(value=float(w1), n_samples=len(anchors), bootstrap=False, source="empirical")


def calibrate_vrp_empirical(
    vrp_realised_by_tenor: dict[str, list[float]],
    defaults_by_tenor: dict[str, float] | None = None,
) -> dict[str, VrpStats]:
    """Per-tenor (mean, std) of an ex-post realised VRP series.

    Feed this with the output of ``core.vol.vrp.compute_realized_vrp``
    grouped per tenor once the history-aligned table is available.
    """
    from core.vol.vrp import VRP_DEFAULTS_VOL_PTS

    defaults = defaults_by_tenor or VRP_DEFAULTS_VOL_PTS["calm"]
    out: dict[str, VrpStats] = {}
    for tenor, series in vrp_realised_by_tenor.items():
        n = len(series)
        if n < MIN_OBSERVATIONS_VRP_PER_TENOR:
            fallback = defaults.get(tenor, 0.8)
            out[tenor] = VrpStats(
                tenor=tenor, mean=float(fallback), std=0.0,
                n_samples=n, bootstrap=True, source="default",
            )
            continue
        mean_ = float(statistics.mean(series))
        std_ = float(statistics.pstdev(series))
        out[tenor] = VrpStats(
            tenor=tenor, mean=mean_, std=std_,
            n_samples=n, bootstrap=False, source="empirical",
        )
    return out


def evaluate_vrp_model_oos(
    y_true: list[float], y_pred: list[float],
) -> dict[str, Any]:
    """Simple OOS diagnostic : MAE + bias + vs-constant baseline improvement."""
    if not y_true or len(y_true) != len(y_pred):
        return {"mae": None, "bias": None, "n": len(y_true), "improvement_vs_const": None}
    errors = [t - p for t, p in zip(y_true, y_pred, strict=True)]
    mae = float(statistics.mean(abs(e) for e in errors))
    bias = float(statistics.mean(errors))
    # Baseline : constant mean of y_true.
    const = float(statistics.mean(y_true))
    baseline_mae = float(statistics.mean(abs(t - const) for t in y_true))
    improvement = (baseline_mae - mae) / baseline_mae if baseline_mae > 1e-9 else 0.0
    return {
        "mae": round(mae, 4),
        "bias": round(bias, 4),
        "n": len(y_true),
        "improvement_vs_const": round(improvement, 4),
    }
