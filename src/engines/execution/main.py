"""Execution engine — FastAPI service interne.

Owns :
  - L'IB connection (clientId=5)
  - La position_sync_loop (1s par défaut)
  - Les endpoints de mutation (/internal/orders, /internal/positions/.../close)
  - L'écriture des order_events (audit log synchrone à chaque action)

Pas exposé via nginx — uniquement appelé depuis le container `api` via
http://execution-engine:8001 sur le réseau interne fxvol-internal.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.order_executor import (
    OrderExecutor,
    OrderExecutorUnavailable,
    OrderRequest,
)
from engines.execution.position_sync import position_sync_loop
from persistence.db import get_sessionmaker
from persistence.models import OrderEvent

logger = logging.getLogger("execution")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

SYNC_INTERVAL_S = float(os.getenv("SYNC_INTERVAL_S", "1.0"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=False)
    app.state.redis = redis

    executor = OrderExecutor(
        host=os.getenv("IB_HOST", "ib-gateway"),
        port=int(os.getenv("IB_PORT", "4002")),
        client_id=int(os.getenv("IB_CLIENT_ID", "5")),
    )
    try:
        await executor.connect(timeout=5.0)
    except Exception:
        logger.exception("ib_connect_failed_at_startup")
    app.state.executor = executor

    sm = get_sessionmaker()
    app.state.sessionmaker = sm

    sync_task = asyncio.create_task(
        position_sync_loop(sm, executor, redis=redis, interval_s=SYNC_INTERVAL_S)
    )
    logger.info("execution_startup ib_connected=%s sync_interval=%.1fs",
                executor.is_connected(), SYNC_INTERVAL_S)
    try:
        yield
    finally:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        await executor.disconnect()
        await redis.aclose()
        logger.info("execution_shutdown")


app = FastAPI(title="fxvol execution-engine", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    ex: OrderExecutor = request.app.state.executor
    return {
        "status": "OK",
        "ib_connected": ex.is_connected(),
        "sync_interval_s": SYNC_INTERVAL_S,
    }


# --- Audit helpers ---------------------------------------------------------

async def _log_event(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    action_type: Literal["SUBMIT", "CANCEL", "CLOSE_POSITION"],
    request_payload: dict,
    response_payload: dict | None,
    success: bool,
    error_message: str | None = None,
) -> None:
    try:
        async with sessionmaker() as db:
            db.add(OrderEvent(
                action_type=action_type,
                request_payload=request_payload,
                response_payload=response_payload,
                success=success,
                error_message=error_message,
                timestamp=datetime.now(UTC),
            ))
            await db.commit()
    except Exception:
        logger.exception("order_event_log_failed action=%s", action_type)


# --- Mutation endpoints ----------------------------------------------------

router = APIRouter(prefix="/internal", tags=["execution"])


def _executor_dep(request: Request) -> OrderExecutor:
    ex: OrderExecutor = request.app.state.executor
    if not ex.is_connected():
        raise HTTPException(status_code=503, detail="IB Gateway not connected")
    return ex


def _sm_dep(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.sessionmaker


class PlaceOrderBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    sec_type: Literal["FUT", "FOP"]
    side: Literal["BUY", "SELL"]
    qty: int = Field(gt=0, le=1000)
    limit_price: float = Field(gt=0)
    expiry: str | None = Field(None, pattern=r"^\d{8}$")
    strike: float | None = Field(None, gt=0)
    right: Literal["C", "P"] | None = None
    exchange: str = "CME"
    currency: str = "USD"
    trading_class: str | None = None


class ClosePositionBody(BaseModel):
    limit_price: float = Field(gt=0)


@router.post("/orders")
async def place_order(
    body: PlaceOrderBody,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    payload = body.model_dump()
    try:
        result = await ex.place_order(OrderRequest(**payload))
        await _log_event(sm, action_type="SUBMIT", request_payload=payload,
                         response_payload=result, success=True)
        return result
    except (OrderExecutorUnavailable, ValueError) as e:
        await _log_event(sm, action_type="SUBMIT", request_payload=payload,
                         response_payload=None, success=False, error_message=str(e)[:500])
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: int,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    payload = {"order_id": order_id}
    try:
        result = await ex.cancel_order(order_id)
        if result is None:
            await _log_event(sm, action_type="CANCEL", request_payload=payload,
                             response_payload=None, success=False,
                             error_message=f"order {order_id} not in open trades")
            raise HTTPException(status_code=404, detail=f"order {order_id} not open")
        await _log_event(sm, action_type="CANCEL", request_payload=payload,
                         response_payload=result, success=True)
        return result
    except OrderExecutorUnavailable as e:
        await _log_event(sm, action_type="CANCEL", request_payload=payload,
                         response_payload=None, success=False, error_message=str(e)[:500])
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/orders")
async def list_orders(ex: Annotated[OrderExecutor, Depends(_executor_dep)]) -> dict[str, Any]:
    orders = await ex.list_open_orders()
    return {"orders": orders, "count": len(orders)}


@router.get("/positions")
async def live_positions(ex: Annotated[OrderExecutor, Depends(_executor_dep)]) -> dict[str, Any]:
    positions = await ex.list_positions()
    return {"positions": positions, "count": len(positions)}


@router.post("/positions/{con_id}/close")
async def close_position(
    con_id: int,
    body: ClosePositionBody,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    payload = {"con_id": con_id, "limit_price": body.limit_price}
    try:
        result = await ex.close_position(con_id=con_id, limit_price=body.limit_price)
        await _log_event(sm, action_type="CLOSE_POSITION", request_payload=payload,
                         response_payload=result, success=True)
        return result
    except (OrderExecutorUnavailable, ValueError) as e:
        await _log_event(sm, action_type="CLOSE_POSITION", request_payload=payload,
                         response_payload=None, success=False, error_message=str(e)[:500])
        raise HTTPException(status_code=400, detail=str(e)) from e


app.include_router(router)
