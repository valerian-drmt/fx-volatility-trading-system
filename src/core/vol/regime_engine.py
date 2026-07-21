"""Step 1 — regime gating compute helpers.

Pure functions only: input dicts/lists, output dicts. No DB / Redis access.
The vol-engine cycle is responsible for fetching history (feature_history,
events, vrp_table_default) and feeding them in. The API uses the same
``gate_decision`` to derive trade authorization from a snapshot.

Cf. docs/vol_trading_pca/specs/STEP1_REGIME_GATING.md §2 + §7.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from core.vol.vrp import detect_regime

EVENT_DAMPENER_DAYS = 5.0
STABILITY_CYCLES = 3


@dataclass(frozen=True)
class GateDecision:
    authorized: bool
    reason: str
    size_mult: float


MIN_OBS_ZSCORE = 30  # below this, σ̂ varies > 50% with sample → false alerts


def compute_rolling_zscore(value: float | None, history: list[float]) -> float | None:
    """Z-score of ``value`` against ``history`` (rolling sample).

    Requires ≥ MIN_OBS_ZSCORE=30 obs. With N<30, σ̂ has > 50% sampling
    variance and any |z| > 2 is trivially obtained — the orange/red
    coloring on the panel would be statistically meaningless. Tradeoff:
    zone 3 z-columns stay grey for the first ~90 minutes of vol-engine
    uptime, in exchange for non-noisy alerts after.
    """
    if value is None or len(history) < MIN_OBS_ZSCORE:
        return None
    mean = sum(history) / len(history)
    var = sum((x - mean) ** 2 for x in history) / max(len(history) - 1, 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    return round((value - mean) / sd, 4)


def qualify(z: float | None, kind: str) -> str | None:
    """Verbal qualifier for a z-score (UI hint). ``kind`` ∈ {level, slope}."""
    if z is None:
        return None
    if kind == "level":
        if z < -1.0:
            return "low"
        if z > 1.0:
            return "high"
        return "normal"
    if z < -1.0:
        return "inverted"
    if z > 1.0:
        return "steep"
    return "flat"


def compute_regime_snapshot(
    *,
    surface: dict[str, Any],
    iv_3m_history_pct: list[float],
    feature_history_rows: list[dict[str, float | None]],
    next_event: dict[str, Any] | None,
    vrp_lookup: dict[tuple[str, str], float],
    now_utc_iso: str,
    gmm_probabilities: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the ``_regime`` payload + the row to persist.

    ``surface`` : dict produced by vol-engine (atm IV per tenor, _rv_full_pct).
    ``iv_3m_history_pct`` : last 30d of iv_atm_3m_pct from feature_history.
    ``feature_history_rows`` : last 90d, used for z-score rolling.
    ``next_event`` : dict {event_type, scheduled_at_iso, days_remaining} or None.
    ``vrp_lookup`` : {(regime, tenor): vrp_vol_pts} from vrp_table_default.

    Returns ``{"payload": {...}, "snapshot_row": {...}, "feature_row": {...}}``.
    """
    iv_1m = _atm_pct(surface, "1M")
    iv_3m = _atm_pct(surface, "3M")
    iv_6m = _atm_pct(surface, "6M")
    rv_yz = surface.get("_rv_full_pct")
    rv_yz = float(rv_yz) if isinstance(rv_yz, (int, float)) else None

    vol_level_pct = iv_3m
    term_slope_pct = (iv_6m - iv_1m) if (iv_1m is not None and iv_6m is not None) else None

    # vol_of_vol = rolling 30d std of iv_3m (needs >= 20 obs).
    vov_pct: float | None = None
    if len(iv_3m_history_pct) >= 20:
        m = sum(iv_3m_history_pct) / len(iv_3m_history_pct)
        vov_pct = round(
            math.sqrt(sum((x - m) ** 2 for x in iv_3m_history_pct) / (len(iv_3m_history_pct) - 1)),
            4,
        )

    # Rolling 90d z-scores on the 3 features.
    z_level = compute_rolling_zscore(
        vol_level_pct,
        [r["vol_level"] for r in feature_history_rows if r.get("vol_level") is not None],
    )
    z_vov = compute_rolling_zscore(
        vov_pct,
        [r["vol_of_vol"] for r in feature_history_rows if r.get("vol_of_vol") is not None],
    )
    z_slope = compute_rolling_zscore(
        term_slope_pct,
        [r["term_slope"] for r in feature_history_rows if r.get("term_slope") is not None],
    )

    # ──────────────────────────────────────────────────────────────────
    # Shadow-mode GMM (Step 1 §13).
    #
    # The active label + method are ALWAYS driven by detect_regime (the
    # threshold heuristic). The GMM may have produced ``gmm_probabilities``
    # — we persist them in regime_snapshots.p_calm/p_stressed/p_pre_event
    # for offline backtest comparison BUT we do NOT expose them in the
    # _regime payload yet (probabilities stays None → frontend zone 2 stays
    # grayed-out as the spec requires until ≥ N obs spanning ≥ 1 traversed
    # event).
    #
    # Why: with a calm-only training window, GMM components don't
    # correspond to real {calm, stressed, pre_event} regimes — the label
    # mapping is mathematically defined but semantically meaningless.
    # See diag run output: 100/0/0 probas are extrapolation artefacts.
    # ──────────────────────────────────────────────────────────────────
    label = detect_regime(
        vol_level_pct=vol_level_pct, vol_of_vol_pct=vov_pct, term_slope_pct=term_slope_pct,
    )
    method = "threshold_heuristic"

    if next_event:
        days = next_event.get("days_remaining")
        event_type = next_event.get("event_type")
        dampener = bool(days is not None and days < EVENT_DAMPENER_DAYS)
    else:
        days = None
        event_type = None
        dampener = False

    vrp_expected = {
        tenor: vrp_lookup.get((label, tenor))
        for tenor in ("1M", "2M", "3M", "4M", "5M", "6M")
    }

    payload = {
        "label": label,
        "method": method,
        # Spec-compliant: null until GMM is promoted out of shadow mode
        # (cf. STEP1 §13). The shadow values are still persisted in
        # regime_snapshots for J+30 backtest comparison.
        "probabilities": None,
        "timestamp": now_utc_iso,
        "features": {
            "vol_level": {
                "value": _round(vol_level_pct, 2), "z": z_level,
                "qualifier": qualify(z_level, "level"),
            },
            "vol_of_vol": {
                "value": _round(vov_pct, 2), "z": z_vov,
                "qualifier": qualify(z_vov, "level"),
            },
            "term_slope": {
                "value": _round(term_slope_pct, 2), "z": z_slope,
                "qualifier": qualify(z_slope, "slope"),
            },
        },
        "next_event": {
            "type": event_type,
            "datetime_utc": next_event.get("scheduled_at_iso") if next_event else None,
            "days_remaining": _round(days, 2),
        },
        "event_dampener": dampener,
        "vrp_expected": {k: float(v) for k, v in vrp_expected.items() if v is not None},
    }

    snapshot_row = {
        "timestamp": now_utc_iso,
        "symbol": surface.get("_symbol", "EURUSD"),
        "label": label,
        "method": method,
        "vol_level_pct": vol_level_pct,
        "vol_of_vol_pct": vov_pct,
        "term_slope_pct": term_slope_pct,
        "vol_level_z": z_level,
        "vol_of_vol_z": z_vov,
        "term_slope_z": z_slope,
        "p_calm": gmm_probabilities.get("calm") if gmm_probabilities else None,
        "p_stressed": gmm_probabilities.get("stressed") if gmm_probabilities else None,
        "p_pre_event": gmm_probabilities.get("pre_event") if gmm_probabilities else None,
        "event_dampener": dampener,
        "days_to_next_event": days,
        "next_event_type": event_type,
    }
    feature_row = {
        "timestamp": now_utc_iso,
        "symbol": surface.get("_symbol", "EURUSD"),
        "iv_atm_1m_pct": iv_1m, "iv_atm_3m_pct": iv_3m, "iv_atm_6m_pct": iv_6m,
        "rv_yz_pct": rv_yz, "vol_of_vol_30d_pct": vov_pct,
        "term_slope_pct": term_slope_pct,
        "vol_level_z90": z_level, "vol_of_vol_z90": z_vov, "term_slope_z90": z_slope,
    }
    return {"payload": payload, "snapshot_row": snapshot_row, "feature_row": feature_row}


def gate_decision(
    label: str, event_dampener: bool, history_labels: list[str],
) -> GateDecision:
    """Step 1 final gate. Cf. STEP1 §2.

    ``history_labels`` = last N labels (most recent first), N ≥ STABILITY_CYCLES.
    """
    recent = history_labels[:STABILITY_CYCLES]
    if len(recent) < STABILITY_CYCLES or any(x != label for x in recent):
        return GateDecision(False, "regime_unstable", 0.0)
    if event_dampener:
        return GateDecision(True, "event_dampener_active", 0.5)
    if label == "pre_event":
        return GateDecision(False, "regime_pre_event", 0.0)
    if label == "stressed":
        return GateDecision(True, "regime_stressed", 0.7)
    if label == "calm":
        return GateDecision(True, "regime_calm", 1.0)
    return GateDecision(False, f"regime_unknown:{label}", 0.0)


def _atm_pct(surface: dict[str, Any], tenor: str) -> float | None:
    node = surface.get(tenor)
    if not isinstance(node, dict):
        return None
    atm = node.get("atm")
    if not isinstance(atm, dict):
        return None
    iv = atm.get("iv")
    if not isinstance(iv, (int, float)):
        return None
    return round(float(iv) * 100.0, 4)


def _round(x: float | None, n: int) -> float | None:
    return round(float(x), n) if isinstance(x, (int, float)) else None
