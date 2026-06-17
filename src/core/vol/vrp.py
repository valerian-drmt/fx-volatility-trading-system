"""Variance Risk Premium (VRP) tables + heuristic regime classifier.

Two surviving public symbols, both consumed by the regime-features pipeline :

- ``VRP_DEFAULTS_VOL_PTS`` : tabulated VRP per (regime, tenor). Read by
  cockpit health metrics and the vol-engine cycle (single source of
  truth ; a mirroring ``vrp_default_curve`` table was dropped in
  migration 038).
- ``detect_regime(vol_level_pct, vol_of_vol_pct, term_slope_pct)`` : 3-regime
  classifier (calm / stressed / pre_event) consumed by ``regime_engine``.

- ``predict_vrp(tenor, regime)`` + ``q_measure_from_p(sigma_p, tenor, regime)`` :
  the P→Q conversion ``σ_fair^Q = σ_fair^P + VRP(tenor, regime)``. Restored
  (R11) for the fair-vol term-structure pipeline (vol-engine consumes them).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Regime = Literal["calm", "stressed", "pre_event"]


# Tabulated from G10 FX literature — EUR/USD in particular. Positive
# values mean the market demands a premium to sell vol, so σ_IV > σ_RV
# on average. Single source of truth ; read by both cockpit (regime
# health response) and vol-engine (per-cycle VRP lookup). A mirroring
# table existed (``vrp_default_curve``) until migration 038 dropped it
# — it was bit-for-bit identical to this dict and never recalibrated.
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
    source: str  # 'default' (tabulated) until a history-calibrated model is live


def predict_vrp(tenor: str, regime: Regime = "calm") -> VrpEstimate:
    """VRP at ``tenor`` for ``regime`` (vol points, e.g. 0.6 = +60bp).

    Returns the tabulated default — empirical calibration needs ≥6 months of
    aligned IV/RV history (future work). Unknown tenor → 0.8 + WARNING.
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
    """σ_fair^Q = σ_fair^P + VRP(tenor, regime). Returns ``(sigma_q_pct, vrp_pts)``."""
    vrp = predict_vrp(tenor, regime)
    return sigma_p_pct + vrp.value_vol_pts, vrp.value_vol_pts


def detect_regime(
    vol_level_pct: float | None = None,
    vol_of_vol_pct: float | None = None,
    term_slope_pct: float | None = None,
) -> Regime:
    """3-regime classifier on volatility features (heuristic v1).

    Definitions :
      - stressed  : sustained high IV (level > 10pp) OR extreme jumpiness (vov > 1pp)
      - pre_event : moderately elevated vol_of_vol (vov > 0.4pp) without
                    sustained level — surface instability that historically
                    precedes high-impact macro releases
      - calm      : neither condition met

    Any ``None`` feature is ignored ; default = "calm". ``term_slope_pct`` is
    currently unused in classification — kept in the signature for backward
    compat with the regime_engine call site.
    """
    del term_slope_pct  # unused : reserved for future term-structure regime
    if vol_level_pct is not None and vol_level_pct > 10.0:
        return "stressed"
    if vol_of_vol_pct is not None and vol_of_vol_pct > 1.0:
        return "stressed"
    if vol_of_vol_pct is not None and vol_of_vol_pct > 0.4:
        return "pre_event"
    return "calm"
