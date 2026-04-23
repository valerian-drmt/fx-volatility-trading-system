"""Pydantic models for /vol/surface, /vol/term-structure, /vol/smile."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SurfaceResponse(BaseModel):
    """Latest vol surface for a symbol — pillars keyed by tenor label.

    ``surface`` values can be either a per-tenor pillar dict, or an
    engine-level aggregate (float / dict prefixed with ``_``).
    """

    symbol: str
    timestamp: datetime
    surface: dict[str, Any]


class TermStructureRow(BaseModel):
    tenor: str
    dte: int | None
    sigma_atm_pct: float | None                    # Q-measure (implied)
    sigma_fair_pct: float | None = None            # legacy — Q if _fair_q exists, else P (GARCH)
    sigma_fair_p_pct: float | None = None          # P-measure (HAR or GARCH)
    sigma_fair_q_pct: float | None = None          # Q-measure (P + VRP)
    vrp_vol_pts: float | None = None               # spread added to P to obtain Q
    regime: str | None = None                      # calm / stressed / pre_event
    rv_pct: float | None = None                    # P-measure realised


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
    sigma_fair_pct: float | None = None
    rv_pct: float | None = None
    svi_curve: list[SmilePoint] | None = None
