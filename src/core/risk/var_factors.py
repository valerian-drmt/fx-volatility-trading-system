"""Scenario VaR by risk factor (R11 G-risk).

Each factor's VaR is the book's loss under its **1-day 99% adverse move**, computed
by full-BS reval (``core.risk.stress.reval_book``) — the exact book P&L under the
shock, not a greek heuristic. So it is derived entirely from the current portfolio;
the only model input is the shock size per factor (desk-typical 1-day 99% moves,
documented below). The historical-vol model variant is deferred to R12
(see ``releases/r11_frontend_backend/13_post_deploy_research_backlog.md``).
"""
from __future__ import annotations

from typing import Any

from core.risk.stress import reval_book

# 1-day 99% adverse factor moves: spot in bp, vol/skew/fly in vol-points.
DEFAULT_SHOCKS: dict[str, float] = {"spot": 150.0, "level": 1.5, "skew": 0.6, "curv": 0.4}
_FACTOR_KW: dict[str, str] = {"spot": "dspot_bp", "level": "dvol_vp", "skew": "dskew_vp", "curv": "dfly_vp"}
_LABELS: dict[str, str] = {"spot": "Spot", "level": "ATM level", "skew": "Skew (RR)", "curv": "Curvature (BF)"}


def factor_var_breakdown(
    baselines: list[dict[str, Any]],
    spot: float,
    shocks: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Per-factor scenario VaR (USD loss) at each factor's 1-day 99% adverse move."""
    sh = shocks or DEFAULT_SHOCKS
    out: list[dict[str, Any]] = []
    for key, kw in _FACTOR_KW.items():
        s = sh.get(key, 0.0)
        up = reval_book(baselines, spot, output="pnl", **{kw: s})
        dn = reval_book(baselines, spot, output="pnl", **{kw: -s})
        out.append({"key": key, "label": _LABELS[key], "var_usd": round(max(0.0, -up, -dn), 2)})
    return out
