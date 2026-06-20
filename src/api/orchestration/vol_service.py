"""Vol endpoints helpers — read latest surface from Redis, historical from Postgres."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis import asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.vol import (
    SmilePoint,
    SmileResponse,
    SurfaceResponse,
    TermStructureResponse,
    TermStructureRow,
)
from bus import keys
from persistence.models import VolSurface

# Smile point extraction — (pillar field for IV, pillar field for strike, label).
_SMILE_ORDER: tuple[tuple[str, str, str], ...] = (
    ("iv_10dp_pct", "strike_10dp", "10P"),
    ("iv_25dp_pct", "strike_25dp", "25P"),
    ("sigma_ATM_pct", "strike_atm", "ATM"),
    ("iv_25dc_pct", "strike_25dc", "25C"),
    ("iv_10dc_pct", "strike_10dc", "10C"),
)


class VolNotFound(Exception):
    """No vol data for the requested (symbol, timestamp, tenor) — caller returns 404."""


async def get_latest_surface(
    redis: aioredis.Redis, symbol: str,
    db: AsyncSession | None = None,
) -> SurfaceResponse:
    """Read latest vol surface — Redis first (TTL 600 s), DB fallback.

    Markets-closed sandbox commonly has the Redis key expired or empty while
    the ``vol_surfaces`` table still holds the last good row. When ``db`` is
    passed and Redis is dry, we fall back to ``ORDER BY timestamp DESC LIMIT 1``.
    Raises ``VolNotFound`` only when both sources are empty.
    """
    raw = await redis.get(keys.LATEST_VOL_SURFACE.format(symbol=symbol))
    if raw:
        payload = json.loads(raw)
        return SurfaceResponse(
            symbol=payload.get("symbol", symbol),
            timestamp=payload["timestamp"],
            surface=payload.get("surface", {}),
        )
    if db is not None:
        stmt = (
            select(VolSurface)
            .where(VolSurface.underlying == symbol)
            .order_by(VolSurface.timestamp.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return SurfaceResponse(
                symbol=row.underlying,
                timestamp=row.timestamp,
                surface=dict(row.surface_data or {}),
            )
    raise VolNotFound(f"No latest vol surface for symbol={symbol}")


async def get_surface_at(
    db: AsyncSession, symbol: str, ts: datetime
) -> SurfaceResponse:
    """Query Postgres ``vol_surfaces`` at an exact timestamp — 404 if missing."""
    stmt = select(VolSurface).where(
        VolSurface.underlying == symbol, VolSurface.timestamp == ts
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise VolNotFound(f"No vol surface at ts={ts.isoformat()} for {symbol}")
    return SurfaceResponse(
        symbol=row.underlying,
        timestamp=row.timestamp,
        surface=dict(row.surface_data or {}),
    )


def _cell_iv_pct(pillar: dict, key: str) -> float | None:
    """IV (in %) of a surface pillar cell (``10dp``/``25dp``/``atm``/``25dc``/
    ``10dc``), or None when the cell / iv is absent. Surface stores fractions."""
    node = pillar.get(key)
    if isinstance(node, dict) and isinstance(node.get("iv"), (int, float)):
        return float(node["iv"]) * 100.0
    return None


def _rr_bf(
    pillar: dict, call_key: str, put_key: str, atm_pct: float | None,
) -> tuple[float | None, float | None]:
    """(risk-reversal, butterfly) in vol points from the pillar wing IVs :
    RR = IV(call) − IV(put) ; BF = ½(IV(call)+IV(put)) − IV(ATM)."""
    c = _cell_iv_pct(pillar, call_key)
    p = _cell_iv_pct(pillar, put_key)
    if c is None or p is None:
        return None, None
    bf = round((c + p) / 2 - atm_pct, 4) if atm_pct is not None else None
    return round(c - p, 4), bf


async def get_term_structure(
    redis: aioredis.Redis, symbol: str, db: AsyncSession | None = None,
) -> TermStructureResponse:
    """Derive the term structure (ATM IV + fair vol per tenor) from the latest
    surface.

    Reads the same source as ``/vol/surface`` — Redis first, then the most
    recent ``vol_surfaces`` row when ``db`` is passed and Redis is dry — so a
    markets-closed sandbox (no live ``latest_vol_surface`` key) still serves the
    last-known term structure instead of 404-ing.

    Fair-vol fields are read from the ``_fair_q`` sub-dict the vol-engine
    publishes (σ_fair^Q = σ_fair^P + VRP, per tenor) + the surface-level
    ``_rv_full_pct``. Tenors without a fair-vol entry keep those fields ``None``
    (e.g. before the engine has enough OHLC history to fit HAR/GARCH).
    """
    surface = await get_latest_surface(redis, symbol, db=db)
    fair_q = surface.surface.get("_fair_q") or {}
    rv_full = surface.surface.get("_rv_full_pct")
    rv_full_f = float(rv_full) if isinstance(rv_full, (int, float)) else None

    rows: list[TermStructureRow] = []
    for tenor, pillar in surface.surface.items():
        if tenor.startswith("_") or not isinstance(pillar, dict):
            continue
        sigma_pct = pillar.get("sigma_atm_pct") or pillar.get("sigma_ATM_pct")
        if sigma_pct is None:
            atm = pillar.get("atm")
            if isinstance(atm, dict) and isinstance(atm.get("iv"), (int, float)):
                sigma_pct = float(atm["iv"]) * 100.0
        fq = fair_q.get(tenor) if isinstance(fair_q, dict) else None
        fq = fq if isinstance(fq, dict) else {}
        sigma_fair_q = fq.get("sigma_fair_q_pct")
        sigma_fair_p = fq.get("sigma_fair_p_pct")
        # Horizon-matched RV per tenor (pillar.rv_pct) ; fall back to the
        # surface-level full-sample RV when the per-tenor window is unavailable.
        pillar_rv = pillar.get("rv_pct")
        rv = float(pillar_rv) if isinstance(pillar_rv, (int, float)) else rv_full_f
        # Smile metrics (vol points) from the surface wings : RR = call − put,
        # BF = ½(call + put) − ATM. None when a wing IV is missing.
        rr25, bf25 = _rr_bf(pillar, "25dc", "25dp", sigma_pct)
        rr10, bf10 = _rr_bf(pillar, "10dc", "10dp", sigma_pct)
        rows.append(
            TermStructureRow(
                tenor=tenor,
                dte=pillar.get("dte"),
                sigma_atm_pct=sigma_pct,
                rr_25d_pct=rr25,
                bf_25d_pct=bf25,
                rr_10d_pct=rr10,
                bf_10d_pct=bf10,
                # legacy field : Q if available, else P.
                sigma_fair_pct=sigma_fair_q if sigma_fair_q is not None else sigma_fair_p,
                sigma_fair_p_pct=sigma_fair_p,
                sigma_fair_q_pct=sigma_fair_q,
                vrp_vol_pts=fq.get("vrp_vol_pts"),
                regime=fq.get("regime"),
                rv_pct=rv,
            )
        )
    return TermStructureResponse(
        symbol=surface.symbol, timestamp=surface.timestamp, pillars=rows
    )


async def get_smile(
    db: AsyncSession, symbol: str, tenor: str
) -> SmileResponse:
    """Return the 5-point smile (10P/25P/ATM/25C/10C) for the latest surface.

    Reads Postgres rather than Redis because the Redis payload is compacted
    (ATM + fair only) while ``vol_surfaces.surface_data`` keeps the full
    pillar dict including delta-strikes.
    """
    stmt = (
        select(VolSurface)
        .where(VolSurface.underlying == symbol)
        .order_by(desc(VolSurface.timestamp))
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise VolNotFound(f"No vol surface history for {symbol}")
    pillar = (row.surface_data or {}).get(tenor)
    if pillar is None:
        raise VolNotFound(f"Tenor {tenor} absent from latest surface for {symbol}")
    # Engine aggregates sit alongside the tenor dicts in surface_data.
    surface_dict = row.surface_data or {}
    garch = surface_dict.get("_garch") or {}
    garch_node = garch.get(tenor) if isinstance(garch, dict) else None
    sigma_fair = (
        float(garch_node["sigma_model_pct"])
        if isinstance(garch_node, dict)
        and isinstance(garch_node.get("sigma_model_pct"), (int, float))
        else None
    )
    rv_raw = surface_dict.get("_rv_full_pct")
    rv_pct = float(rv_raw) if isinstance(rv_raw, (int, float)) else None
    points = list(_smile_points(pillar))
    svi_curve = _fit_svi_if_possible(points, tenor, pillar, row.spot)
    return SmileResponse(
        symbol=row.underlying,
        timestamp=row.timestamp,
        tenor=tenor,
        dte=pillar.get("dte"),
        points=points,
        sigma_fair_pct=sigma_fair,
        rv_pct=rv_pct,
        svi_curve=svi_curve,
    )


# Approximate year-fraction per tenor label — matches engines.vol.engine.
_TENOR_YEARS: dict[str, float] = {
    "1W": 7 / 365, "1M": 1 / 12, "2M": 2 / 12,
    "3M": 3 / 12, "4M": 4 / 12, "5M": 5 / 12, "6M": 6 / 12, "1Y": 1.0,
}


def _fit_svi_if_possible(
    points: list[SmilePoint], tenor: str, pillar: dict[str, Any], spot: Any,
) -> list[SmilePoint] | None:
    """Run SVI fit on the observed points ; return a 40-point curve or None.

    The curve is sampled between ``min(strike_obs)`` and ``max(strike_obs)``
    so its endpoints sit exactly on the 10P and 10C pillars — no visual
    extrapolation past the observed data, and every tenor's fit spans
    the same observed range.
    """
    import math

    if len(points) < 3 or spot is None:
        return None
    try:
        from core.vol.svi import fit_svi, svi_curve
    except ImportError:
        return None
    T = _TENOR_YEARS.get(tenor)
    if T is None:
        return None
    strikes = [p.strike for p in points]
    ivs = [p.iv_pct / 100.0 for p in points]
    try:
        forward = float(spot)
    except (TypeError, ValueError):
        return None
    params = fit_svi(strikes, ivs, forward=forward, tenor_years=T)
    if params is None:
        return None
    # Sample the fit on the observed strike window so the first/last curve
    # points land exactly on the 10P / 10C pillars.
    k_min = math.log(min(strikes) / forward)
    k_max = math.log(max(strikes) / forward)
    curve = svi_curve(forward, T, params, k_min=k_min, k_max=k_max, n_points=40)
    return [SmilePoint(strike=p["strike"], iv_pct=p["iv_pct"], delta_label="SVI") for p in curve]


def _smile_points(pillar: dict[str, Any]):
    """Yield SmilePoint for each delta available on this pillar.

    Supports two shapes :
    - legacy flat : ``{sigma_ATM_pct, strike_atm, iv_25dc_pct, strike_25dc, ...}``
    - engine nested : ``{atm: {iv, strike}, 25dc: {iv, strike}, 25dp: ..., 10dc: ..., 10dp: ...}``
      (``iv`` is a decimal — converted × 100 to a percent for the response).
    """
    nested_map: tuple[tuple[str, str], ...] = (
        ("10dp", "10P"),
        ("25dp", "25P"),
        ("atm", "ATM"),
        ("25dc", "25C"),
        ("10dc", "10C"),
    )
    # Try flat first to preserve back-compat.
    flat_yielded = False
    for iv_key, strike_key, label in _SMILE_ORDER:
        iv = pillar.get(iv_key)
        strike = pillar.get(strike_key)
        if iv is None or strike is None:
            continue
        flat_yielded = True
        yield SmilePoint(strike=strike, iv_pct=iv, delta_label=label)
    if flat_yielded:
        return
    # Fall back to the nested engine shape.
    for node_key, label in nested_map:
        node = pillar.get(node_key)
        if not isinstance(node, dict):
            continue
        iv = node.get("iv")
        strike = node.get("strike")
        if iv is None or strike is None:
            continue
        yield SmilePoint(
            strike=float(strike), iv_pct=float(iv) * 100.0, delta_label=label,
        )
