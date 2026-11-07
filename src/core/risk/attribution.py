"""P&L attribution maths (pure). Hot/cold T4 (#368).

Per-leg Taylor decomposition of a window P&L into δ·dS / ½Γ·dS² / V·dσ / Θ·dt
/ residual, the pivot into group matrices, the source filter and the small
unit helpers. Moved out of ``api.routers.portfolio_panel`` so the analytics
engine can compute the attribution once per cycle (``persistence.rollups``)
and the api reads one row; both call these same functions, so the numbers
cannot drift between the writer and the reader.

Deterministic for a given ``now``; no I/O.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from core.vol.tenors import tenor_bucket_from_expiry

ATTRIB_GROUP_KEYS: dict[str, str] = {"tenor": "attrib_tenor", "structure": "structure_type", "wing": "wing"}
ATTRIB_TERMS = ["delta_pnl_usd", "gamma_pnl_usd", "vega_pnl_usd", "theta_pnl_usd"]
ATTRIB_SUM_COLS = ["actual_pnl_usd", *ATTRIB_TERMS]
# Tenor ladder order; buckets outside it (2W, other, …) follow, sorted.
ATTRIB_TENOR_LADDER = ("1M", "2M", "3M", "4M", "5M", "6M")
# Smile order for the wing pivot: downside → ATM → upside, then linear / catch-all.
ATTRIB_WING_ORDER = ("Put wing", "Body (ATM)", "Call wing", "Future / delta-1", "other")
ATTRIB_SOURCES = ("ib_live", "booked", "all")
_TERM_KEYS = ("actual_pnl_usd", "delta_pnl_usd", "gamma_pnl_usd", "vega_pnl_usd", "theta_pnl_usd", "residual_usd")


def attrib_tenor_bucket(expiry: date | None, today: date) -> str | None:
    """The 1M..6M ladder by DTE — one implementation in core, shared with
    ``/positions/open`` (``tenor_now``) so the Position breakdown and the
    attribution by tenor put a leg in the same bucket (#377)."""
    return tenor_bucket_from_expiry(expiry, today)


def filter_source(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Keep the rows of one source. The by-trade panel always showed IB legs
    only while the by-tenor matrix summed IB + booked, so their totals could
    differ; both now read the same filtered set (#377)."""
    if source == "all":
        return rows
    return [r for r in rows if r.get("source") == source]


def iv_fraction_to_pts(iv: float | None) -> float | None:
    """IB-live ``open_position.iv`` is a FRACTION (0.0605) while ``vega_usd`` is
    $ per VOL POINT, so V·dσ needs dσ in points: ×100. Without it the vega term
    was 100× too small and its P&L leaked into the residual (#377)."""
    return None if iv is None else float(iv) * 100.0


def spot_at_or_before(bars: list[dict[str, Any]] | None, cutoff: datetime) -> float | None:
    """Close of the latest candle that OPENED at or before ``cutoff``
    (``t`` = bar-open epoch ms, as the market-data engine stores them)."""
    if not bars:
        return None
    cutoff_ms = cutoff.timestamp() * 1000.0
    best: float | None = None
    best_t = float("-inf")
    for b in bars:
        try:
            t, c = float(b["t"]), float(b["c"])
        except (KeyError, TypeError, ValueError):
            continue
        if t <= cutoff_ms and t > best_t:
            best, best_t = c, t
    return best


def decompose_row(
    *,
    now: datetime,
    pnl_now: float | None, pnl_then: float | None,
    spot_now: float | None, spot_then: float | None,
    iv_now: float | None, iv_then: float | None,
    delta: float | None, gamma: float | None,
    vega: float | None, theta: float | None,
    t_then: datetime | None,
    delta_then: float | None = None, gamma_then: float | None = None,
    vega_then: float | None = None, theta_then: float | None = None,
    gamma_scale: float = 1.0,
) -> dict[str, float | None]:
    """Taylor-decompose one leg's window P&L into δ·dS / ½Γ·dS² / V·dσ / Θ·dt / residual.

    Anchors on the t-1 (window-start) greek, falling back to the current one when
    a t-1 column is null. ``gamma_scale`` converts the stored gamma into Γ_$ (2nd
    derivative in raw spot²) so ½Γ·dS² is real dollars: IB-live ``gamma_usd`` is
    $/pip (``1/PIP_SIZE``), booked ``gamma_usd_per_pip2`` is $/pip²
    (``1/PIP_SIZE²``).
    """
    actual = (pnl_now - pnl_then) if (pnl_now is not None and pnl_then is not None) else None
    # No t-1 snapshot → the leg's realized P&L over the window is unmeasurable,
    # so there is nothing to attribute. Suppress every term (else a theoretical
    # delta·dS would show against a $0 actual and pollute the trade/tenor sums).
    if actual is None:
        return dict.fromkeys(_TERM_KEYS, None)
    dspot = (spot_now - spot_then) if (spot_now is not None and spot_then is not None) else None
    div_pts = (iv_now - iv_then) if (iv_now is not None and iv_then is not None) else None
    dt_days = ((now - t_then).total_seconds() / 86400.0) if t_then is not None else None
    d = delta_then if delta_then is not None else delta
    g = gamma_then if gamma_then is not None else gamma
    v = vega_then if vega_then is not None else vega
    th = theta_then if theta_then is not None else theta
    delta_pnl = (d * dspot) if (d is not None and dspot is not None) else None
    gamma_pnl = (0.5 * g * gamma_scale * dspot * dspot) if (g is not None and dspot is not None) else None
    vega_pnl = (v * div_pts) if (v is not None and div_pts is not None) else None
    theta_pnl = (th * dt_days) if (th is not None and dt_days is not None) else None
    explained: float | None
    if None in (delta_pnl, gamma_pnl, vega_pnl, theta_pnl):
        explained = None
    else:
        explained = float(delta_pnl) + float(gamma_pnl) + float(vega_pnl) + float(theta_pnl)
    residual = (actual - explained) if (actual is not None and explained is not None) else None
    return {
        "actual_pnl_usd": round(actual, 2) if actual is not None else None,
        "delta_pnl_usd": round(delta_pnl, 2) if delta_pnl is not None else None,
        "gamma_pnl_usd": round(gamma_pnl, 2) if gamma_pnl is not None else None,
        "vega_pnl_usd": round(vega_pnl, 2) if vega_pnl is not None else None,
        "theta_pnl_usd": round(theta_pnl, 2) if theta_pnl is not None else None,
        "residual_usd": round(residual, 2) if residual is not None else None,
    }


def attribution_groups(
    per_position: list[dict[str, Any]],
    group_by: str,
) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    """Pivot per-position Taylor terms into an attribution matrix.

    A bucket accumulates a term only from the legs that actually carry it. A term
    no leg could measure — every contributor ``None`` because it has no t-1
    snapshot in the window (typically a leg opened inside it) — stays ``None``
    rather than summing to ``0.0``. An unmeasurable P&L is not a flat P&L, and a
    bucket of manufactured zeros is indistinguishable from a genuinely flat one.
    Only buckets the book actually holds are emitted.
    """
    key_col = ATTRIB_GROUP_KEYS[group_by]
    buckets: dict[str, dict[str, float | None]] = {}
    for row in per_position:
        label = str(row.get(key_col) or "other")
        b = buckets.setdefault(label, dict.fromkeys(ATTRIB_SUM_COLS, None))
        for c in ATTRIB_SUM_COLS:
            if row.get(c) is not None:
                b[c] = (b[c] or 0.0) + float(row[c])

    def _mk(label: str, b: dict[str, float | None]) -> dict[str, Any]:
        terms = [b[c] for c in ATTRIB_TERMS]
        # The residual only foots when the actual AND every term it is derived
        # from are known — otherwise it would silently absorb the missing ones.
        residual = (
            b["actual_pnl_usd"] - sum(float(t) for t in terms)  # type: ignore[arg-type,operator]
            if b["actual_pnl_usd"] is not None and all(t is not None for t in terms)
            else None
        )
        out: dict[str, Any] = {"label": label}
        for c in ATTRIB_SUM_COLS:
            out[c] = round(b[c], 2) if b[c] is not None else None
        out["residual_usd"] = round(residual, 2) if residual is not None else None
        return out

    if group_by == "tenor":
        head = [t for t in ATTRIB_TENOR_LADDER if t in buckets]
    elif group_by == "wing":
        head = [w for w in ATTRIB_WING_ORDER if w in buckets]
    else:
        head = sorted(buckets, key=lambda g: abs(buckets[g]["actual_pnl_usd"] or 0.0), reverse=True)
    order = head + sorted(g for g in buckets if g not in head)
    groups = [_mk(label, buckets[label]) for label in order]
    totals = {
        c: (round(sum(vals), 2) if (vals := [g[c] for g in groups if g[c] is not None]) else None)
        for c in [*ATTRIB_SUM_COLS, "residual_usd"]
    }
    return groups, totals


def attribution_totals(per_position: list[dict[str, Any]]) -> dict[str, float | None]:
    """Sum each term across the legs that carry it (``None`` when none does)."""
    def _sum(key: str) -> float | None:
        vals = [row[key] for row in per_position if row.get(key) is not None]
        return round(sum(vals), 2) if vals else None
    return {k: _sum(k) for k in _TERM_KEYS}


def assemble_attribution(
    per_position: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    source: str,
    group_by: str | None,
) -> dict[str, Any]:
    """The ``/portfolio/pnl-attribution`` payload from a stored (or freshly
    computed) per-position decomposition: filter on source, then either the
    pivot (``group_by``) or totals + per_position. Pure, so the api reads one
    row and shapes it here; ``group_by`` is validated by the caller."""
    rows = filter_source(per_position, source)
    body = {**meta, "source": source}
    if group_by is not None:
        groups, totals = attribution_groups(rows, group_by)
        return {"group_by": group_by, **body, "groups": groups, "totals": totals}
    return {**body, "totals": attribution_totals(rows), "per_position": rows}
