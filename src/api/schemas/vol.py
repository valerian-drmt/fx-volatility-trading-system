"""Pydantic models for /vol/surface, /vol/term-structure, /vol/smile."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# Where a vol payload came from (#328, audit 05#10):
#   live           the vol engine's last cycle, read from the Redis key (TTL 10 min)
#   last-complete  the most recent full grid persisted in Postgres (market closed,
#                  engine down, Redis cold)
#   historical     an explicit ``/vol/surface/at/{ts}`` read
#   synthetic      a made-up sandbox surface (the preview's manual mode only)
VolSource = Literal["live", "last-complete", "historical", "synthetic"]

# A surface older than this is flagged stale whatever its source: two vol
# cycles plus margin, the same window as the desk's freshness badge.
STALE_AFTER_S: float = 600.0


class StalenessFields(BaseModel):
    """Every vol payload says how old it is and where it came from, so no
    consumer re-stamps it with the fetch time (09#1) or trusts a synthetic
    grid as live (05#10)."""

    as_of: datetime
    source: VolSource
    stale: bool
    age_s: float


class SurfaceResponse(StalenessFields):
    """Latest vol surface for a symbol — pillars keyed by tenor label.

    ``surface`` values can be either a per-tenor pillar dict, or an
    engine-level aggregate (float / dict prefixed with ``_``).
    ``timestamp`` is kept as the historical name of ``as_of``.
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
    rv_pct: float | None = None                    # P-measure realised (horizon-matched)
    # Smile metrics from the surface wings (vol points) :
    rr_25d_pct: float | None = None                # IV(25Δc) − IV(25Δp) (skew)
    bf_25d_pct: float | None = None                # ½(IV(25Δc)+IV(25Δp)) − IV(ATM) (wings)
    rr_10d_pct: float | None = None                # 10Δ risk reversal
    bf_10d_pct: float | None = None                # 10Δ butterfly


class TermStructureResponse(StalenessFields):
    symbol: str
    timestamp: datetime
    pillars: list[TermStructureRow]


class SmilePoint(BaseModel):
    """One point of the smile : strike + implied vol in percent."""

    strike: float
    iv_pct: float
    delta_label: str  # '10P', '25P', 'ATM', '25C', '10C'


class SmileResponse(StalenessFields):
    symbol: str
    timestamp: datetime
    tenor: str
    dte: int | None
    points: list[SmilePoint]
    sigma_fair_pct: float | None = None
    rv_pct: float | None = None
    svi_curve: list[SmilePoint] | None = None
