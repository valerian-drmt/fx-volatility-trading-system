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
    rows: list[TermStructureRow] = []
    for tenor, pillar in surface.surface.items():
        # Skip engine-only aggregates that are not per-tenor pillars.
        if tenor.startswith("_") or not isinstance(pillar, dict):
            continue
        # Two shapes supported :
        #  1. {"sigma_atm_pct": 7.2, "dte": 7}                (legacy / DB)
        #  2. {"atm": {"iv": 0.072, "strike": 1.17}, "25dc": ...}  (engine/Redis)
        sigma_pct = pillar.get("sigma_atm_pct") or pillar.get("sigma_ATM_pct")
        if sigma_pct is None:
            atm = pillar.get("atm")
            if isinstance(atm, dict) and isinstance(atm.get("iv"), (int, float)):
                sigma_pct = float(atm["iv"]) * 100.0
        rows.append(
            TermStructureRow(tenor=tenor, dte=pillar.get("dte"), sigma_atm_pct=sigma_pct)
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
