"""Variance Risk Premium (VRP) tables + heuristic regime classifier.

Two surviving public symbols, both consumed by the regime-features pipeline :

- ``VRP_DEFAULTS_VOL_PTS`` : tabulated VRP per (regime, tenor). Used by
  cockpit health metrics and the alembic seed for ``vrp_default_curve``.
- ``detect_regime(vol_level_pct, vol_of_vol_pct, term_slope_pct)`` : 3-regime
  classifier (calm / stressed / pre_event) consumed by ``regime_engine``.

The Q-measure conversion (``q_measure_from_p``, ``predict_vrp``) and ex-post
``compute_realized_vrp`` were retired in R9 alongside the per-tenor pricing
signal pipeline — no live consumers remained.
"""
from __future__ import annotations

from typing import Literal

Regime = Literal["calm", "stressed", "pre_event"]


# Tabulated from G10 FX literature — EUR/USD in particular. Positive values =
# market demands premium to sell vol, so σ_IV > σ_RV on average. Used by
# cockpit (read-only health) and alembic 010 seed for vrp_default_curve.
VRP_DEFAULTS_VOL_PTS: dict[Regime, dict[str, float]] = {
    "calm":      {"1M": 0.6, "2M": 0.7, "3M": 0.8, "4M": 0.9, "5M": 1.0, "6M": 1.1},
    "stressed":  {"1M": 1.5, "2M": 1.6, "3M": 1.8, "4M": 1.9, "5M": 2.0, "6M": 2.1},
    "pre_event": {"1M": 2.5, "2M": 2.2, "3M": 2.0, "4M": 1.9, "5M": 1.8, "6M": 1.8},
}


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
