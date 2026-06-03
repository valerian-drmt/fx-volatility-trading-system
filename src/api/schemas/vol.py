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
