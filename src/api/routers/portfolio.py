"""GET /api/v1/{positions,risk,pnl-curve,history} — portfolio read endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from api.models.portfolio import (
    GreeksAggregated,
    HistoryResponse,
    PnLCurve,
    PositionView,
)
from api.services import portfolio_service as svc

router = APIRouter(prefix="/api/v1", tags=["portfolio"])

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/positions", response_model=list[PositionView])
async def list_positions(
    db: DbDep,
    status: str | None = Query(None, pattern="^(OPEN|CLOSED|EXPIRED)$"),
    limit: int = Query(100, ge=1, le=500),
) -> list[PositionView]:
    """All positions (most-recent first). Filter by ``status`` if given."""
    return await svc.list_positions(db, status=status, limit=limit)


@router.get("/positions/{position_id}", response_model=PositionView)
async def get_position(position_id: int, db: DbDep) -> PositionView:
    try:
        return await svc.get_position(db, position_id)
    except svc.NotFoundInPortfolio as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/risk", response_model=GreeksAggregated)
async def portfolio_risk(redis: RedisDep) -> GreeksAggregated:
    """Portfolio-level greeks published by RiskEngine every cycle (~2s)."""
    try:
        return await svc.get_greeks(redis)
    except svc.NotFoundInPortfolio as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/pnl-curve", response_model=PnLCurve)
async def pnl_curve(redis: RedisDep) -> PnLCurve:
    try:
        return await svc.get_pnl_curve(redis)
    except svc.NotFoundInPortfolio as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/history", response_model=HistoryResponse)
async def position_history(
    db: DbDep,
    position_id: int = Query(..., ge=1),
    limit: int = Query(500, ge=1, le=5000),
) -> HistoryResponse:
    """Snapshots for a position, oldest-first (ready for timeseries plotting)."""
    try:
        return await svc.get_history(db, position_id, limit=limit)
    except svc.NotFoundInPortfolio as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
