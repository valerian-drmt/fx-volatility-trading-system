"""Display tenor pillars + listed→display interpolation (surface tenor change).

The desk shows a fixed set of standard FX-vol pillars (1M, 2M, 3M, 6M, 9M, 1Y),
but CME lists options only at discrete expiries (weekly/monthly serials near-term,
quarterly Mar/Jun/Sep/Dec further out). So a display pillar is either:

* ``listed``  — a real listed expiry sits at (≈) the pillar's target DTE, or
* ``interp``  — no listed contract; the smile is interpolated from the bracketing
  listed expiries (total-variance-linear in calendar time), or
* ``missing`` — the pillar is beyond the furthest listed expiry (no honest value).

See ``docs/surface_tenor_pillars.md``. Pure module — no I/O, fully unit-tested.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# The 6 standard display pillars + their target DTE (days). Single source of truth.
DISPLAY_PILLARS: tuple[str, ...] = ("1M", "2M", "3M", "6M", "9M", "1Y")
PILLAR_TARGET_DTE: dict[str, int] = {
    "1M": 30, "2M": 60, "3M": 90, "6M": 180, "9M": 270, "1Y": 365,
}
# Delta pillars per tenor (put wings → ATM → call wings).
DELTA_PILLARS: tuple[str, ...] = ("10dp", "25dp", "atm", "25dc", "10dc")

# A listed expiry within this many days of a pillar's target counts as "listed".
LISTED_TOLERANCE_DAYS: int = 12
# Past the furthest listed expiry, hold the last anchor flat up to this margin
# (marked interp); beyond it the pillar is "missing" — never extrapolate freely.
EXTRAP_MARGIN_DAYS: int = 45


@dataclass(frozen=True)
class TenorAnchor:
    """One listed expiry's smile: actual DTE + per-delta IV (fraction, e.g. 0.071)."""
    dte: int
    iv_by_pillar: dict[str, float]


def _interp_iv(iv_lo: float, dte_lo: int, iv_hi: float, dte_hi: int, target: int) -> float:
    """Interpolate IV at ``target`` DTE via total variance (σ²·t) linear in calendar
    time — the arbitrage-reasonable convention for the ATM/term direction."""
    w_lo = iv_lo * iv_lo * dte_lo
    w_hi = iv_hi * iv_hi * dte_hi
    frac = (target - dte_lo) / (dte_hi - dte_lo)
    w = w_lo + (w_hi - w_lo) * frac
    return math.sqrt(max(w, 0.0) / target) if target > 0 else iv_lo


def interpolate_pillar(
    anchors: list[TenorAnchor],
    target_dte: int,
    *,
    delta_pillars: tuple[str, ...] = DELTA_PILLARS,
    tol_days: int = LISTED_TOLERANCE_DAYS,
    extrap_margin_days: int = EXTRAP_MARGIN_DAYS,
) -> tuple[dict[str, float] | None, str]:
    """Resolve one display pillar from the listed anchors.

    Returns ``(iv_by_delta | None, source)`` where source ∈ {listed, interp, missing}.
    Each delta is interpolated independently. A delta absent from a bracketing
    anchor is skipped (no value for that cell).
    """
    if not anchors:
        return None, "missing"
    ordered = sorted(anchors, key=lambda a: a.dte)

    # 1. listed — a real expiry within tolerance of the target.
    nearest = min(ordered, key=lambda a: abs(a.dte - target_dte))
    if abs(nearest.dte - target_dte) <= tol_days:
        return dict(nearest.iv_by_pillar), "listed"

    # 2. interp — bracketed by a shorter and a longer listed expiry.
    lo = [a for a in ordered if a.dte < target_dte]
    hi = [a for a in ordered if a.dte > target_dte]
    if lo and hi:
        a_lo, a_hi = lo[-1], hi[0]
        out: dict[str, float] = {}
        for d in delta_pillars:
            iv_lo, iv_hi = a_lo.iv_by_pillar.get(d), a_hi.iv_by_pillar.get(d)
            if iv_lo is not None and iv_hi is not None:
                out[d] = _interp_iv(iv_lo, a_lo.dte, iv_hi, a_hi.dte, target_dte)
        return (out, "interp") if out else (None, "missing")

    # 3. just past the furthest anchor — hold it flat within the margin, else missing.
    if lo and not hi:
        a_lo = lo[-1]
        if target_dte - a_lo.dte <= extrap_margin_days:
            return dict(a_lo.iv_by_pillar), "interp"
    return None, "missing"


def nearest_listed_dte(target_dte: int, listed_dtes: list[int]) -> int | None:
    """The listed expiry an order at ``target_dte`` snaps to — nearest by |ΔDTE|
    (the standard listed-options convention). ``None`` if no listed expiry."""
    return min(listed_dtes, key=lambda d: abs(d - target_dte)) if listed_dtes else None
