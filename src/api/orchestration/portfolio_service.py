"""Portfolio endpoint helpers — positions + snapshots from PG, greeks/pnl from Redis."""
from __future__ import annotations

import json

from redis import asyncio as aioredis
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.portfolio import (
    GreeksAggregated,
    HistoryResponse,
    PnLCurve,
    PositionSnapshotView,
    PositionView,
)
from bus import keys
from persistence.models import OpenPosition, OpenPositionHistory


class NotFoundInPortfolio(Exception):
    """Router maps this to 404 (position missing, no greeks yet, etc.)."""


async def list_positions(
    db: AsyncSession, status: str | None = None, limit: int = 100
) -> list[PositionView]:
    """List positions ordered by most recent entry first.

    The ``status`` parameter is accepted for OpenAPI back-compat but
    ignored : after migration 028, only OPEN positions live in the table
    (closed positions are DELETEd at sync time).
    """
    _ = status
    stmt = select(OpenPosition).order_by(desc(OpenPosition.entry_timestamp)).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [PositionView.model_validate(r) for r in rows]


async def get_position(db: AsyncSession, position_id: int) -> PositionView:
    row = await db.get(OpenPosition, position_id)
    if row is None:
        raise NotFoundInPortfolio(f"Position {position_id} not found")
    return PositionView.model_validate(row)


async def get_greeks(redis: aioredis.Redis) -> GreeksAggregated:
    """Read ``latest_greeks:portfolio`` from Redis (TTL 30s)."""
    raw = await redis.get(keys.LATEST_GREEKS_PORTFOLIO)
    if not raw:
        raise NotFoundInPortfolio("No latest greeks available yet")
    payload = json.loads(raw)
    return GreeksAggregated(timestamp=payload["timestamp"], greeks=payload.get("greeks", {}))


async def get_pnl_curve(redis: aioredis.Redis) -> PnLCurve:
    raw = await redis.get(keys.LATEST_PNL_CURVE)
    if not raw:
        raise NotFoundInPortfolio("No latest pnl curve available yet")
    payload = json.loads(raw)
    return PnLCurve(timestamp=payload["timestamp"], curve=payload.get("curve", {}))


async def get_history(
    db: AsyncSession, position_id: int, limit: int = 500
) -> HistoryResponse:
    """Latest N snapshots for a position, chronological (oldest first for plotting)."""
    if await db.get(OpenPosition, position_id) is None:
        raise NotFoundInPortfolio(f"Position {position_id} not found")
    stmt = (
        select(OpenPositionHistory)
        .where(OpenPositionHistory.position_id == position_id)
        .order_by(asc(OpenPositionHistory.timestamp))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return HistoryResponse(
        position_id=position_id,
        snapshots=[PositionSnapshotView.model_validate(r) for r in rows],
    )
