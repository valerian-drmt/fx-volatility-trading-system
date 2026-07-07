"""GET /api/v1/{vol-history,system-stats} — analytics reads.

``/vol-history`` surfaces vol_surface_snapshot headline fields ;
``/system-stats`` reports row counts + engine heartbeats.
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from api.orchestration import analytics_service as svc
from api.schemas.analytics import (
    BarRow,
    SystemStats,
    VolHistoryRow,
)
from bus import keys

# range presets the market-data engine caches (bus.BARS keys)
_BAR_TIMEFRAMES = ("1D", "1W", "1M")

router = APIRouter(prefix="/api/v1", tags=["analytics"])

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/vol-history", response_model=list[VolHistoryRow])
async def vol_history(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    # cap raised 1000→5000 so the ticker can bucket ~8 days of spot-mid snapshots
    # into 48 × 4H candles (constraint is not surfaced in the OpenAPI TS types)
    limit: int = Query(50, ge=1, le=5000),
) -> list[VolHistoryRow]:
    """N most recent vol_surface_snapshot rows for ``symbol`` — headline fields only."""
    return await svc.vol_history(db, symbol=symbol, limit=limit)


@router.get("/bars", response_model=list[BarRow])
async def bars(
    redis: RedisDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    tf: str = Query("1D"),
    limit: int = Query(250, ge=1, le=500),
) -> list[BarRow]:
    """Real OHLC candles for ``symbol``/``tf`` (range preset 1D/1W/1M) from the
    market-data engine's Redis cache (IB ``reqHistoricalData``, MIDPOINT). Empty
    list until the engine has populated the cache (needs IB Gateway + engines)."""
    if tf not in _BAR_TIMEFRAMES:
        return []
    raw = await redis.get(keys.BARS.format(symbol=symbol, timeframe=tf))
    if not raw:
        return []
    rows = json.loads(raw)
    return [BarRow(**r) for r in rows[-limit:]]


@router.get("/system-stats", response_model=SystemStats)
async def system_stats(db: DbDep, redis: RedisDep) -> SystemStats:
    """Combined view : PG row counts + engine heartbeat ages from Redis."""
    return await svc.system_stats(db, redis)
