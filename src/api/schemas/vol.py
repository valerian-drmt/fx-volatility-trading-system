"""Pydantic models for /vol/surface, /vol/term-structure, /vol/smile."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SurfaceResponse(BaseModel):
    """Latest vol surface for a symbol — pillars keyed by tenor label."""

    symbol: str
    timestamp: datetime
    surface: dict[str, dict[str, Any]]


class TermStructureRow(BaseModel):
    tenor: str
    dte: int | None
    sigma_atm_pct: float | None
    # Smile metrics in vol points, from the surface wings (live when the
    # engine publishes per-tenor 25Δ/10Δ wing IVs) :
    #   RR = IV(call) − IV(put) ; BF = ½(IV(call)+IV(put)) − IV(ATM).
    rr_25d_pct: float | None = None
    bf_25d_pct: float | None = None
    rr_10d_pct: float | None = None
    bf_10d_pct: float | None = None
    # Fair vol per tenor (σ_fair^P, σ_fair^Q = P + VRP) + horizon-matched RV.
    # Schema-ahead : null until the vol-engine publishes `_fair_q` / `rv_pct`
    # (model-fit enrichment). `sigma_fair_pct` = Q when available, else P.
    sigma_fair_pct: float | None = None
    sigma_fair_p_pct: float | None = None
    sigma_fair_q_pct: float | None = None
    vrp_vol_pts: float | None = None
    regime: str | None = None
    rv_pct: float | None = None


class TermStructureResponse(BaseModel):
    symbol: str
    timestamp: datetime
    pillars: list[TermStructureRow]


class SmilePoint(BaseModel):
    """One point of the smile : strike + implied vol in percent."""

    strike: float
    iv_pct: float
    delta_label: str  # '10P', '25P', 'ATM', '25C', '10C'


class SmileResponse(BaseModel):
    symbol: str
    timestamp: datetime
    tenor: str
    dte: int | None
    points: list[SmilePoint]
