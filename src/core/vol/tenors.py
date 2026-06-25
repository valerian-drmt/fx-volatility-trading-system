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


# Nominal DTE for each engine tenor label (the engine buckets actual expiries into
# these labels via chain_fetcher.tenor_label). Used to place anchors on the time axis.
LABEL_DTE: dict[str, int] = {
    "1M": 30, "2M": 60, "3M": 90, "4M": 120, "5M": 150, "6M": 180, "9M": 270, "1Y": 365,
}


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
        out[pillar] = {d: {"iv": iv, "strike": None, "source": source} for d, iv in iv_by.items()}

    _recompute_surface_z(out)
    return out
