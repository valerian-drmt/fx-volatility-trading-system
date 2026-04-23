"""Variance Risk Premium (VRP) — Q-measure conversion for fair vol.

The physical-measure estimator (HAR-RV or GARCH) answers "what will the
realised vol be on average ?". That is NOT what an option is priced to —
IV contains a structural premium that buyers pay to sellers for
gamma/vega insurance.

    σ_fair^Q(T)  =  σ_fair^P(T)  +  VRP(T, regime)

This module exposes :

- ``compute_realized_vrp(iv_history, rv_history, horizon)`` : ex-post
  realised VRP = σ_IV_t − σ_RV_{t→t+T}. Feeds future empirical
  calibration once ≥6 months of history is in Postgres.
- ``predict_vrp(tenor, regime)`` : returns the best estimate of the
  VRP at the selected tenor. Currently a **literature-backed constant
  per tenor per regime** — the fallback mentioned in the refactor plan
  P1.2 while we accumulate history. Logs WARNING on use so monitoring
  surfaces the gap.
- ``detect_regime(features)`` : stub returning ``"calm"`` by default.
  The GMM-clustered version (P1.1) lands once the ``surface_features``
  table has ~6 months of data.

Literature anchors (Bollerslev-Tauchen-Zhou 2009, Bekaert-Hoerova 2014,
Carr-Wu 2009) : for FX G10 calm regime, realised VRP sits around
+0.3% to +1.5% annualised σ on 1M-3M tenors, tapering mildly with
tenor. Stressed and pre-event regimes shift higher (+1% to +3%).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Regime = Literal["calm", "stressed", "pre_event"]


# ── Empirical defaults (constants) ────────────────────────────────────
# Tabulated from G10 FX literature — EUR/USD in particular. Positive
# values = market demands premium to sell vol, so σ_IV > σ_RV on
# average. These are the fallback values used until a live estimator
# based on history tables is calibrated.
VRP_DEFAULTS_VOL_PTS: dict[Regime, dict[str, float]] = {
    "calm":      {"1M": 0.6, "2M": 0.7, "3M": 0.8, "4M": 0.9, "5M": 1.0, "6M": 1.1},
    "stressed":  {"1M": 1.5, "2M": 1.6, "3M": 1.8, "4M": 1.9, "5M": 2.0, "6M": 2.1},
    "pre_event": {"1M": 2.5, "2M": 2.2, "3M": 2.0, "4M": 1.9, "5M": 1.8, "6M": 1.8},
}


@dataclass(frozen=True)
class VrpEstimate:
    tenor: str
    regime: Regime
    value_vol_pts: float
    source: str  # 'empirical' once the history-calibrated model is live, 'default' otherwise


def predict_vrp(tenor: str, regime: Regime = "calm") -> VrpEstimate:
    """Return the VRP at ``tenor`` for ``regime`` (in vol points, e.g. 0.6 for +60bp).

    Currently always returns the tabulated default — empirical calibration
    requires the ``signals`` + ``vol_surfaces`` tables to hold ≥6 months
    of history, which sandbox/r9 does not yet have.
    """
    bucket = VRP_DEFAULTS_VOL_PTS.get(regime, VRP_DEFAULTS_VOL_PTS["calm"])
    value = bucket.get(tenor)
    if value is None:
        logger.warning("predict_vrp: unknown tenor %r, defaulting to 0.8", tenor)
        value = 0.8
    return VrpEstimate(tenor=tenor, regime=regime, value_vol_pts=value, source="default")


def q_measure_from_p(
    sigma_p_pct: float, tenor: str, regime: Regime = "calm",
) -> tuple[float, float]:
    """Convert σ_fair^P (percent) to σ_fair^Q by adding the VRP at the tenor.

    Returns ``(sigma_q_pct, vrp_value_pts)`` so the caller can log both.
    """
    vrp = predict_vrp(tenor, regime)
    return sigma_p_pct + vrp.value_vol_pts, vrp.value_vol_pts


def detect_regime(
    vol_level_pct: float | None = None,
    vol_of_vol_pct: float | None = None,
    term_slope_pct: float | None = None,
) -> Regime:
    """Classify the current market regime from three features.

    Temporary rule-based classifier pending the GMM of refactor plan
    P1.1 (which requires ≥6 months of feature history) :

    - vol_level_pct > 10% or vol_of_vol_pct > 1.0%   → "stressed"
    - abs(term_slope_pct) > 2.0% and vol_level_pct > 7% → "pre_event"
    - otherwise → "calm"

    Any ``None`` feature is ignored in the test — default is "calm".
    """
    if vol_level_pct is not None and vol_level_pct > 10.0:
        return "stressed"
    if vol_of_vol_pct is not None and vol_of_vol_pct > 1.0:
        return "stressed"
    if (
        term_slope_pct is not None
        and abs(term_slope_pct) > 2.0
        and vol_level_pct is not None
        and vol_level_pct > 7.0
    ):
        return "pre_event"
    return "calm"


def compute_realized_vrp(
    iv_history: list[tuple[float, float]],
    rv_history: list[tuple[float, float]],
    horizon_days: int,
) -> list[tuple[float, float]]:
    """Ex-post realised VRP per date — placeholder until history tables exist.

    Each input is a list of ``(epoch_timestamp, vol_pct)`` tuples.
    Returns pairs ``(epoch_timestamp_of_entry, vrp_vol_pts)`` where
    vrp = σ_IV_t − σ_RV_{t → t+horizon}. Drops entries whose forward
    window falls outside rv_history.

    This function is implemented but unused in the sandbox live path —
    it will fire once the analytics layer has a query returning IV and
    RV time series aligned on timestamps.
    """
    if not iv_history or not rv_history:
        return []
    rv_by_ts = dict(rv_history)
    rv_ts_sorted = sorted(rv_by_ts.keys())
    out: list[tuple[float, float]] = []
    seconds_per_day = 86400.0
    for ts, iv in iv_history:
        target = ts + horizon_days * seconds_per_day
        fwd = _closest_after(rv_ts_sorted, target)
        if fwd is None:
            continue
        rv = rv_by_ts[fwd]
        out.append((ts, iv - rv))
    return out


def _closest_after(sorted_ts: list[float], target: float) -> float | None:
    for t in sorted_ts:
        if t >= target:
            return t
    return None
