"""Fair-vol term-structure assembly (R11) — pure P→Q over a surface dict.

The vol-engine computes the P-measure estimators (HAR-RV / GARCH) + the
full-sample RV and stashes them on the surface payload under ``_har`` /
``_garch`` / ``_rv_full_pct``. This module turns those into the Q-measure
fair vol per tenor by adding the VRP — ``σ_fair^Q = σ_fair^P + VRP(tenor,
regime)`` — and is the single place the assembly lives (pure, unit-tested ;
recovered from the v1 vol-engine, git b45d9a6).
"""
from __future__ import annotations

from typing import Any

from core.vol.vrp import detect_regime, q_measure_from_p


def pick_sigma_fair_p(
    surface: dict[str, Any], tenor: str, preferred_estimator: str,
) -> float | None:
    """Return σ_fair^P (percent) for ``tenor`` using ``preferred_estimator``
    ('har' or 'garch'), falling back to the other estimator if absent."""
    har = surface.get("_har") or {}
    garch = surface.get("_garch") or {}
    order = (har, garch) if preferred_estimator == "har" else (garch, har)
    for bucket in order:
        node = bucket.get(tenor) if isinstance(bucket, dict) else None
        if not isinstance(node, dict):
            continue
        for key in ("sigma_har_pct", "sigma_model_pct"):
            v = node.get(key)
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _pillar_rv(surface: dict[str, Any], tenor: str) -> float | None:
    """Horizon-matched Yang-Zhang RV (%) stashed on the pillar by the engine."""
    pillar = surface.get(tenor)
    if isinstance(pillar, dict):
        v = pillar.get("rv_pct")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def build_fair_q(
    surface: dict[str, Any], preferred_estimator: str = "rv",
) -> dict[str, dict[str, float]]:
    """Attach σ_fair^Q per tenor : ``σ_fair^Q = σ_fair^P + VRP(tenor, regime)``.

    σ_fair^P is **anchored to the Yang-Zhang realised vol** — horizon-matched
    per tenor (``pillar.rv_pct``) when present, else the full-sample
    ``_rv_full_pct``. The HAR-RV / GARCH forecasts (``_har`` / ``_garch``) are
    kept on the surface as forward-looking diagnostics but are NOT the fair
    level : their daily-|return| RV proxy is biased low vs the OHLC-range
    Yang-Zhang estimator (and the log-space projection adds a retransformation
    bias), which drove σ_fair to ~half of RV. RV + VRP is the robust,
    defensible fair (implied ≳ realised by the variance risk premium). HAR/GARCH
    are used only as a last resort when no RV at all is available.

    Returns ``{tenor: {sigma_fair_p_pct, vrp_vol_pts, sigma_fair_q_pct, regime,
    fair_source}}``. A tenor is skipped only when it has neither RV nor estimator.
    """
    rv_pct = surface.get("_rv_full_pct")
    rv_full = float(rv_pct) if isinstance(rv_pct, (int, float)) else None
    atm_1m = ((surface.get("1M") or {}).get("atm") or {}).get("iv")
    atm_6m = ((surface.get("6M") or {}).get("atm") or {}).get("iv")
    slope = None
    if isinstance(atm_1m, (int, float)) and isinstance(atm_6m, (int, float)):
        slope = (float(atm_6m) - float(atm_1m)) * 100.0
    regime = detect_regime(vol_level_pct=rv_full, vol_of_vol_pct=None, term_slope_pct=slope)
    fallback = preferred_estimator if preferred_estimator in ("har", "garch") else "har"
    out: dict[str, dict[str, float]] = {}
    for tenor in surface:
        if tenor.startswith("_") or not isinstance(surface[tenor], dict):
            continue
        rv_t = _pillar_rv(surface, tenor)
        if rv_t is not None:
            sigma_p, source = rv_t, "rv_tenor"
        elif rv_full is not None:
            sigma_p, source = rv_full, "rv_full"
        else:
            sigma_p, source = pick_sigma_fair_p(surface, tenor, fallback), fallback
        if sigma_p is None:
            continue
        sigma_q, vrp = q_measure_from_p(sigma_p, tenor=tenor, regime=regime)
        out[tenor] = {
            "sigma_fair_p_pct": round(sigma_p, 4),
            "vrp_vol_pts": round(vrp, 4),
            "sigma_fair_q_pct": round(sigma_q, 4),
            "regime": regime,
            "fair_source": source,
        }
    return out
