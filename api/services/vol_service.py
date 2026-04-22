"""Vol endpoints helpers — read latest surface from Redis, historical from Postgres."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis import asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.vol import (
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
    redis: aioredis.Redis, symbol: str
) -> SurfaceResponse:
    """Read ``latest_vol_surface:{symbol}`` from Redis — 404 if empty."""
    raw = await redis.get(keys.LATEST_VOL_SURFACE.format(symbol=symbol))
    if not raw:
        raise VolNotFound(f"No latest vol surface for symbol={symbol}")
    payload = json.loads(raw)
    return SurfaceResponse(
        symbol=payload.get("symbol", symbol),
        timestamp=payload["timestamp"],
        surface=payload.get("surface", {}),
    )


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


async def get_term_structure(
    redis: aioredis.Redis, symbol: str
) -> TermStructureResponse:
    """Derive term structure (tenor → ATM vol) from the latest Redis surface."""
    surface = await get_latest_surface(redis, symbol)
    # Engine aggregates : _fair_q (Q-measure per tenor), _har + _garch
    # (P-measure per tenor), and _rv_full_pct (P-measure aggregate).
    fair_q_all = surface.surface.get("_fair_q") or {}
    har = surface.surface.get("_har") or {}
    garch = surface.surface.get("_garch") or {}
    rv_pct = surface.surface.get("_rv_full_pct")
    rv_pct = float(rv_pct) if isinstance(rv_pct, (int, float)) else None

    rows: list[TermStructureRow] = []
    for tenor, pillar in surface.surface.items():
        if tenor.startswith("_") or not isinstance(pillar, dict):
            continue
        sigma_pct = pillar.get("sigma_atm_pct") or pillar.get("sigma_ATM_pct")
        if sigma_pct is None:
            atm = pillar.get("atm")
            if isinstance(atm, dict) and isinstance(atm.get("iv"), (int, float)):
                sigma_pct = float(atm["iv"]) * 100.0
        # P-measure : prefer HAR, fall back to GARCH.
        sigma_fair_p = None
        for bucket, key in ((har, "sigma_har_pct"), (garch, "sigma_model_pct")):
            node = bucket.get(tenor) if isinstance(bucket, dict) else None
            if isinstance(node, dict) and isinstance(node.get(key), (int, float)):
                sigma_fair_p = float(node[key])
                break
        # Q-measure : authoritative _fair_q, else fall back to P.
        fair_q_node = fair_q_all.get(tenor) if isinstance(fair_q_all, dict) else None
        if isinstance(fair_q_node, dict):
            sigma_fair_q = _float_or_none(fair_q_node.get("sigma_fair_q_pct"))
            vrp = _float_or_none(fair_q_node.get("vrp_vol_pts"))
            regime = fair_q_node.get("regime")
        else:
            sigma_fair_q = sigma_fair_p
            vrp = None
            regime = None
        # Legacy ``sigma_fair_pct`` : keep the previous semantics (Q if we
        # have one, else whatever P estimator is available). Lets old
        # frontends keep rendering.
        sigma_fair_legacy = sigma_fair_q if sigma_fair_q is not None else sigma_fair_p
        rows.append(
            TermStructureRow(
                tenor=tenor,
                dte=pillar.get("dte"),
                sigma_atm_pct=sigma_pct,
                sigma_fair_pct=sigma_fair_legacy,
                sigma_fair_p_pct=sigma_fair_p,
                sigma_fair_q_pct=sigma_fair_q,
                vrp_vol_pts=vrp,
                regime=str(regime) if regime else None,
                rv_pct=rv_pct,
            )
        )
    return TermStructureResponse(
        symbol=surface.symbol, timestamp=surface.timestamp, pillars=rows
    )


def _float_or_none(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) else None


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


# Approximate year-fraction per tenor label — matches services.vol.engine.
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
