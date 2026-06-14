"""GET /api/v1/{vol-history,system-stats} — analytics reads.

The /signals + /backtest routes were dropped in R9 with the per-tenor
pricing-signals retirement. /vol-history surfaces vol_surface_history
headline fields ; /system-stats reports row counts + engine heartbeats.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from api.orchestration import analytics_service as svc
from api.schemas.analytics import (
    SystemStats,
    VolHistoryRow,
)

router = APIRouter(prefix="/api/v1", tags=["analytics"])

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/vol-history", response_model=list[VolHistoryRow])
async def vol_history(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    limit: int = Query(50, ge=1, le=1000),
) -> list[VolHistoryRow]:
    """N most recent vol_surface_snapshot rows for ``symbol`` — headline fields only."""
    return await svc.vol_history(db, symbol=symbol, limit=limit)


@router.get("/system-stats", response_model=SystemStats)
async def system_stats(db: DbDep, redis: RedisDep) -> SystemStats:
    """Combined view : PG row counts + engine heartbeat ages from Redis."""
    return await svc.system_stats(db, redis)
