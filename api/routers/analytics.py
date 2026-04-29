"""GET /api/v1/{signals,vol-history,backtest,system-stats} — analytics reads."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from api.models.analytics import (
    BacktestRunRow,
    SignalRow,
    SystemStats,
    VolHistoryRow,
)
from api.services import analytics_service as svc

router = APIRouter(prefix="/api/v1", tags=["analytics"])

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/signals", response_model=list[SignalRow])
async def signals(
    db: DbDep,
    underlying: str | None = Query(None, min_length=3, max_length=20),
    tenor: str | None = Query(None, max_length=5),
    signal_type: str | None = Query(None, pattern="^(CHEAP|EXPENSIVE|FAIR)$"),
    since: datetime | None = None,
    limit: int = Query(200, ge=1, le=2000),
) -> list[SignalRow]:
    """Recent signals, most-recent first. Filters combinable (underlying, tenor, type, since)."""
    return await svc.list_signals(
        db, underlying=underlying, tenor=tenor,
        signal_type=signal_type, since=since, limit=limit,
    )


@router.get("/vol-history", response_model=list[VolHistoryRow])
async def vol_history(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    limit: int = Query(50, ge=1, le=1000),
) -> list[VolHistoryRow]:
    """N most recent vol_surfaces snapshots — headline fields only (no JSONB payload)."""
    return await svc.vol_history(db, symbol=symbol, limit=limit)


@router.get("/backtest", response_model=list[BacktestRunRow])
async def backtests(
    db: DbDep,
    strategy_name: str | None = Query(None, max_length=50),
    limit: int = Query(50, ge=1, le=500),
) -> list[BacktestRunRow]:
    """Backtest runs with headline metrics (Sharpe, MDD, return, n_trades)."""
    return await svc.list_backtests(db, strategy_name=strategy_name, limit=limit)


@router.get("/system-stats", response_model=SystemStats)
async def system_stats(db: DbDep, redis: RedisDep) -> SystemStats:
    """Combined view : PG row counts + engine heartbeat ages from Redis."""
    return await svc.system_stats(db, redis)
