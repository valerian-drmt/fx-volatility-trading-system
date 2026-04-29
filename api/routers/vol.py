"""GET /api/v1/vol/* — surface from Redis, historical + smile from Postgres."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from api.models.vol import SmileResponse, SurfaceResponse, TermStructureResponse
from api.services import vol_service

router = APIRouter(prefix="/api/v1/vol", tags=["vol"])

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/surface", response_model=SurfaceResponse)
async def latest_surface(
    redis: RedisDep, symbol: str = Query("EURUSD", min_length=3, max_length=20)
) -> SurfaceResponse:
    """Latest volatility surface for ``symbol`` — reads Redis cache (TTL 600s)."""
    try:
        return await vol_service.get_latest_surface(redis, symbol)
    except vol_service.VolNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/surface/at/{ts}", response_model=SurfaceResponse)
async def surface_at(
    ts: datetime, db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
) -> SurfaceResponse:
    """Historical surface at exact timestamp — reads Postgres ``vol_surfaces``."""
    try:
        return await vol_service.get_surface_at(db, symbol, ts)
    except vol_service.VolNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/term-structure", response_model=TermStructureResponse)
async def term_structure(
    redis: RedisDep, symbol: str = Query("EURUSD", min_length=3, max_length=20)
) -> TermStructureResponse:
    """Tenor → ATM vol mapping derived from the latest Redis surface."""
    try:
        return await vol_service.get_term_structure(redis, symbol)
    except vol_service.VolNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/smile/{tenor}", response_model=SmileResponse)
async def smile(
    tenor: str, db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
) -> SmileResponse:
    """5-point smile (10P/25P/ATM/25C/10C) for a tenor from the latest PG row."""
    try:
        return await vol_service.get_smile(db, symbol, tenor)
    except vol_service.VolNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
