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


def _wing_iv_pct(pillar: dict, delta: str) -> float | None:
    """IV (%) of a surface wing cell, robust to both pillar layouts : the flat
    ``iv_{delta}_pct`` the engine currently publishes, or a nested
    ``{delta: {"iv": fraction}}`` form."""
    flat = pillar.get(f"iv_{delta}_pct")
    if isinstance(flat, (int, float)):
        return float(flat)
    node = pillar.get(delta)
    if isinstance(node, dict) and isinstance(node.get("iv"), (int, float)):
        return float(node["iv"]) * 100.0
    return None


def _rr_bf(
    pillar: dict, call: str, put: str, atm_pct: float | None
) -> tuple[float | None, float | None]:
    """(risk-reversal, butterfly) in vol points from the wing IVs :
    RR = IV(call) − IV(put) ; BF = ½(IV(call)+IV(put)) − IV(ATM)."""
    c, p = _wing_iv_pct(pillar, call), _wing_iv_pct(pillar, put)
    if c is None or p is None:
        return None, None
    bf = round((c + p) / 2 - atm_pct, 4) if atm_pct is not None else None
    return round(c - p, 4), bf


async def get_term_structure(
    redis: aioredis.Redis, symbol: str
) -> TermStructureResponse:
    """Term structure from the latest Redis surface : ATM IV + smile metrics
    (RR/BF, live from the wings) per tenor, plus fair-vol / RV fields read from
    the engine enrichment (``_fair_q`` per tenor, ``_rv_full_pct``, pillar
    ``rv_pct``) — these stay null until the vol-engine publishes them."""
    surface = await get_latest_surface(redis, symbol)
    fair_q = surface.surface.get("_fair_q")
    fair_q = fair_q if isinstance(fair_q, dict) else {}
    rv_full = surface.surface.get("_rv_full_pct")
    rv_full_f = float(rv_full) if isinstance(rv_full, (int, float)) else None

    rows: list[TermStructureRow] = []
    for tenor, pillar in surface.surface.items():
        if tenor.startswith("_") or not isinstance(pillar, dict):
            continue  # skip meta keys (_regime, _pca_signals, _symbol, …)
        atm = pillar.get("sigma_atm_pct") or pillar.get("sigma_ATM_pct")
        rr25, bf25 = _rr_bf(pillar, "25dc", "25dp", atm)
        rr10, bf10 = _rr_bf(pillar, "10dc", "10dp", atm)
        fq = fair_q.get(tenor)
        fq = fq if isinstance(fq, dict) else {}
        sf_q, sf_p = fq.get("sigma_fair_q_pct"), fq.get("sigma_fair_p_pct")
        pillar_rv = pillar.get("rv_pct")
        rv = float(pillar_rv) if isinstance(pillar_rv, (int, float)) else rv_full_f
        rows.append(
            TermStructureRow(
                tenor=tenor,
                dte=pillar.get("dte"),
                sigma_atm_pct=atm,
                rr_25d_pct=rr25,
                bf_25d_pct=bf25,
                rr_10d_pct=rr10,
                bf_10d_pct=bf10,
                sigma_fair_pct=sf_q if sf_q is not None else sf_p,
                sigma_fair_p_pct=sf_p,
                sigma_fair_q_pct=sf_q,
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
    return SmileResponse(
        symbol=row.underlying,
        timestamp=row.timestamp,
        tenor=tenor,
        dte=pillar.get("dte"),
        points=list(_smile_points(pillar)),
    )


def _smile_points(pillar: dict[str, Any]):
    """Yield SmilePoint for each delta available on this pillar."""
    for iv_key, strike_key, label in _SMILE_ORDER:
        iv = pillar.get(iv_key)
        strike = pillar.get(strike_key)
        if iv is None or strike is None:
            continue
        yield SmilePoint(strike=strike, iv_pct=iv, delta_label=label)
