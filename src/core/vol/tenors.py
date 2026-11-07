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
from datetime import date

# The 6 display pillars + their target DTE (days). Single source of truth.
# Matches CME's listed monthly-serial range (1M..6M). 1M-5M are real listed
# expiries; 6M is interpolated (source="interp") until a ~180d serial lists,
# then flips to "listed" automatically. (9M/1Y are quarterly-only and not
# currently listed — left out to avoid permanently-empty columns.)
DISPLAY_PILLARS: tuple[str, ...] = ("1M", "2M", "3M", "4M", "5M", "6M")
# THE tenor map (#331, A6-03): label -> nominal calendar days. Every other
# module derives from it (``tenor_dte`` / ``tenor_years`` / ``nearest_tenor``);
# the five private copies it replaced disagreed on 1M (30 d vs 1/12 y).
LABEL_DTE: dict[str, int] = {
    "1M": 30, "2M": 60, "3M": 90, "4M": 120, "5M": 150, "6M": 180, "9M": 270, "1Y": 365,
}
PILLAR_TARGET_DTE: dict[str, int] = {t: LABEL_DTE[t] for t in DISPLAY_PILLARS}


def tenor_dte(tenor: str) -> int | None:
    """Nominal days to expiry of a tenor label; None for an unknown label."""
    return LABEL_DTE.get(tenor)


def tenor_years(tenor: str) -> float | None:
    """Nominal year fraction (ACT/365) of a tenor label; None when unknown."""
    d = LABEL_DTE.get(tenor)
    return None if d is None else d / 365.0


def nearest_tenor(days: int, labels: tuple[str, ...] = DISPLAY_PILLARS) -> str:
    """The label whose nominal DTE is closest to ``days`` (ties -> the shorter)."""
    return min(labels, key=lambda t: (abs(LABEL_DTE[t] - days), LABEL_DTE[t]))
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


# The fixed attribution / display ladder, and the DTE midpoints between the
# nominal pillar DTEs (30/60/90/120/150/180) that bucket a position onto it.
TENOR_LADDER: tuple[str, ...] = ("1M", "2M", "3M", "4M", "5M", "6M")
_LADDER_EDGES: tuple[tuple[int, str], ...] = (
    (45, "1M"), (75, "2M"), (105, "3M"), (135, "4M"), (165, "5M"),
)


def tenor_bucket_from_expiry(expiry: date | None, today: date) -> str | None:
    """Bucket a position onto the 1M..6M ladder by its days to expiry (#377).

    This is THE tenor a panel shows for an open position: ``open_position.tenor``
    is the label it was entered with and never rolls (a 3M option is still
    tagged "3M" the week it expires; a 1W option carried its own out-of-order
    row), so the Position breakdown and the P&L attribution by tenor used to
    disagree about which bucket a leg sits in. Anything under ~1M clamps into
    1M, anything past ~6M into 6M, so only the six ladder pillars ever appear.
    """
    if expiry is None:
        return None
    dte = (expiry - today).days
    for edge, label in _LADDER_EDGES:
        if dte <= edge:
            return label
    return "6M"


def nearest_listed_dte(target_dte: int, listed_dtes: list[int]) -> int | None:
    """The listed expiry an order at ``target_dte`` snaps to — nearest by |ΔDTE|
    (the standard listed-options convention). ``None`` if no listed expiry."""
    return min(listed_dtes, key=lambda d: abs(d - target_dte)) if listed_dtes else None


def _tenor_dte(tenor: str) -> int | None:
    return tenor_dte(tenor)


def surface_listed_tenors(surface: dict) -> list[str]:
    """Tenors in a (display) surface that have a real listed contract — present
    IV and NOT flagged ``source="interp"``. Meta keys (``_*``) are ignored."""
    out: list[str] = []
    for t, row in surface.items():
        if not isinstance(t, str) or t.startswith("_") or not isinstance(row, dict):
            continue
        cells = [c for c in row.values() if isinstance(c, dict)]
        has_iv = any(isinstance(c.get("iv"), (int, float)) for c in cells)
        is_interp = any(c.get("source") == "interp" for c in cells)
        if has_iv and not is_interp:
            out.append(t)
    return out


def snap_tenor(requested: str, surface: dict) -> tuple[str, bool]:
    """Resolve an order's requested tenor to a tradeable one.

    Returns ``(effective_tenor, snapped)``: if ``requested`` is itself listed →
    unchanged (snapped False). If it's interpolated / absent → the nearest LISTED
    tenor by |ΔDTE| (snapped True). If nothing is listed (or DTE unknown) →
    ``requested`` unchanged (caller handles the degraded case).
    """
    listed = surface_listed_tenors(surface)
    if requested in listed:
        return requested, False
    target = _tenor_dte(requested)
    candidates = [(t, _tenor_dte(t)) for t in listed]
    candidates = [(t, d) for t, d in candidates if d is not None]
    if target is None or not candidates:
        return requested, False
    best = min(candidates, key=lambda td: abs(td[1] - target))
    return best[0], True


def _cell_strikes(row: dict) -> dict[str, float]:
    return {
        d: float(c["strike"]) for d, c in row.items()
        if isinstance(c, dict) and isinstance(c.get("strike"), (int, float)) and c["strike"] > 0
    }


def _interp_strikes(raw_cells: dict[str, dict], target_dte: int) -> dict[str, float]:
    """Per-delta strike at ``target_dte``, linear in DTE between the two listed
    expiries that bracket it; held flat past the furthest one. Empty when the
    listed cells carry no strikes."""
    dated = sorted(
        ((LABEL_DTE[lbl], _cell_strikes(row)) for lbl, row in raw_cells.items() if lbl in LABEL_DTE),
        key=lambda x: x[0],
    )
    dated = [(dte, ks) for dte, ks in dated if ks]
    if not dated:
        return {}
    lo = [x for x in dated if x[0] < target_dte]
    hi = [x for x in dated if x[0] > target_dte]
    if lo and hi:
        (d0, k0), (d1, k1) = lo[-1], hi[0]
        frac = (target_dte - d0) / (d1 - d0)
        return {
            d: round(k0[d] + (k1[d] - k0[d]) * frac, 6)
            for d in k0 if d in k1
        }
    if lo:
        return dict(lo[-1][1])
    return dict(hi[0][1])


def _recompute_surface_z(display: dict[str, dict]) -> None:
    """Attach a cross-sectional z to every display cell: z = (iv − mean)/std over
    all (tenor × delta) IVs of the display grid. No-op on a flat/degenerate grid."""
    ivs = [
        c["iv"] for t, row in display.items()
        if not t.startswith("_") and isinstance(row, dict)
        for c in row.values()
        if isinstance(c, dict) and isinstance(c.get("iv"), (int, float))
    ]
    if len(ivs) < 2:
        return
    mean = sum(ivs) / len(ivs)
    var = sum((x - mean) ** 2 for x in ivs) / len(ivs)
    std = math.sqrt(var)
    if std <= 0:
        return
    for t, row in display.items():
        if t.startswith("_") or not isinstance(row, dict):
            continue
        for c in row.values():
            if isinstance(c, dict) and isinstance(c.get("iv"), (int, float)):
                c["z"] = (c["iv"] - mean) / std


def to_display_surface(
    surface: dict,
    *,
    delta_pillars: tuple[str, ...] = DELTA_PILLARS,
    tol_days: int = LISTED_TOLERANCE_DAYS,
) -> dict:
    """Re-key a raw listed-tenor surface to the 6 display pillars.

    A pillar that matches a listed tenor (within tol) keeps that tenor's real
    cells (iv + strike + …) tagged ``source="listed"``; otherwise its IVs are
    interpolated (``source="interp"``, no strike — there is no contract); a pillar
    past the furthest anchor is omitted (frontend renders "—"). Meta keys (``_svi``,
    ``_regime`, …) are carried through untouched. z is recomputed over the display
    grid. Returns a NEW dict; the input is not mutated.
    """
    import copy

    raw_cells: dict[str, dict] = {}
    anchors: list[TenorAnchor] = []
    for label, row in surface.items():
        if not isinstance(label, str) or label.startswith("_") or not isinstance(row, dict):
            continue
        dte = LABEL_DTE.get(label)
        if dte is None:
            continue
        iv_by = {
            d: c["iv"] for d, c in row.items()
            if isinstance(c, dict) and isinstance(c.get("iv"), (int, float))
        }
        if iv_by:
            raw_cells[label] = row
            anchors.append(TenorAnchor(dte=dte, iv_by_pillar=iv_by))

    # carry meta keys through
    out: dict = {k: v for k, v in surface.items() if isinstance(k, str) and k.startswith("_")}

    for pillar in DISPLAY_PILLARS:
        target = PILLAR_TARGET_DTE[pillar]
        # listed — reuse the real cells (keep strike, etc.)
        if raw_cells:
            nearest = min(raw_cells, key=lambda lbl: abs(LABEL_DTE[lbl] - target))
            if abs(LABEL_DTE[nearest] - target) <= tol_days:
                cell = copy.deepcopy(raw_cells[nearest])
                for c in cell.values():
                    # Idempotent: a cell already flagged interp (e.g. a 2nd pass
                    # over an already-display surface) stays interp — never
                    # downgrade an interpolated pillar to "listed".
                    if isinstance(c, dict) and c.get("source") != "interp":
                        c["source"] = "listed"
                out[pillar] = cell
                continue
        # interp / missing
        iv_by, source = interpolate_pillar(
            anchors, target, delta_pillars=delta_pillars, tol_days=tol_days,
        )
        if iv_by is None:
            continue  # missing — omit; frontend shows "—"
        # An interpolated pillar has no contract, but it does have a strike
        # per delta: linear in DTE between the bracketing listed expiries
        # (held flat past the last one). Without it every consumer that keys
        # on strike dropped the pillar and fell back to a constant IV (#310).
        strike_by = _interp_strikes(raw_cells, target)
        out[pillar] = {
            d: {"iv": iv, "strike": strike_by.get(d), "source": source}
            for d, iv in iv_by.items()
        }

    _recompute_surface_z(out)
    return out
