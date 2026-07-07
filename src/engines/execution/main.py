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

from engines.execution.hedge_executor import HedgeSubmitError, submit_hedge_order
from engines.execution.ib_heartbeat import (
    heartbeat_loop,
    mark_disconnected,
    stuck_order_watcher_loop,
)
from engines.execution.live_submit import LiveSubmitError, submit_structure_live
from engines.execution.order_executor import (
    OrderExecutor,
    OrderExecutorUnavailable,
    OrderRequest,
)
from engines.execution.position_sync import position_sync_loop
from engines.execution.reaper import reaper_loop
from engines.execution.redis_state import set_client as set_redis_client
from engines.execution.rollback_runner import run_rollback
from persistence.db import get_sessionmaker
from persistence.models import TradeEvent
from shared.logging import configure_logging
from shared.observability import start_metrics_server
from shared.tracing import init_tracing

# T1 logs unification : structlog JSON output like the other engines.
# Replaces logging.basicConfig (which produced console-format lines that
# Promtail couldn't parse to extract `level` for Loki).
configure_logging(service_name="execution_engine", level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("execution")

# P0 obs : Prometheus /metrics endpoint port. Spec § Phase 0 step 3.
_METRICS_PORT = 9104

# 5 s default : UI polls positions at ~5 s, moderate IB load (~12 calls/min).
# Override via the SYNC_INTERVAL_S env var.
SYNC_INTERVAL_S = float(os.getenv("SYNC_INTERVAL_S", "5.0"))
HEARTBEAT_INTERVAL_S = float(os.getenv("HEARTBEAT_INTERVAL_S", "10.0"))
STUCK_WATCH_INTERVAL_S = float(os.getenv("STUCK_WATCH_INTERVAL_S", "60.0"))
STUCK_AFTER_S = float(os.getenv("STUCK_AFTER_S", "600.0"))
# Order FSM liveness (invariant I2) : reaper terminalises working orders that
# exceed REAPER_TAU_S and are no longer live at IB. Spec §6.2.
REAPER_INTERVAL_S = float(os.getenv("REAPER_INTERVAL_S", "30.0"))
REAPER_TAU_S = float(os.getenv("REAPER_TAU_S", "300.0"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # P0 obs : start the Prometheus /metrics HTTP server first thing so the
    # endpoint is reachable even during the rest of startup.
    start_metrics_server(_METRICS_PORT, engine="execution_engine")
    # P2 obs : OTel tracer init (post P2.1 validation).
    init_tracing(service_name="execution_engine")

    redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=False)
    app.state.redis = redis
    set_redis_client(redis)

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
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(sm, executor, interval_s=HEARTBEAT_INTERVAL_S),
        name="ib_heartbeat_loop",
    )
    stuck_task = asyncio.create_task(
        stuck_order_watcher_loop(
            sm,
            interval_s=STUCK_WATCH_INTERVAL_S,
            stuck_after_seconds=STUCK_AFTER_S,
        ),
        name="stuck_order_watcher_loop",
    )
    reaper_task = asyncio.create_task(
        reaper_loop(
            sm,
            executor,
            interval_s=REAPER_INTERVAL_S,
            tau_stale_s=REAPER_TAU_S,
        ),
        name="order_reaper_loop",
    )
    logger.info(
        "execution_startup ib_connected=%s sync_interval=%.1fs heartbeat=%.1fs",
        executor.is_connected(), SYNC_INTERVAL_S, HEARTBEAT_INTERVAL_S,
    )
    try:
        yield
    finally:
        for task in (sync_task, heartbeat_task, stuck_task, reaper_task):
            task.cancel()
        for task in (sync_task, heartbeat_task, stuck_task, reaper_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            async with sm() as db:
                await mark_disconnected(db, datetime.now(UTC))
                await db.commit()
        except Exception:
            logger.exception("mark_disconnected_failed")
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
    action_type: Literal["SUBMIT", "CANCEL", "CLOSE_POSITION", "CLOSE_POSITION_BY_SYMBOL", "SYNC_POSITIONS"],
    request_payload: dict,
    response_payload: dict | None,
    success: bool,
    error_message: str | None = None,
) -> None:
    # order_events folded into the trade_event journal (migration 044).
    try:
        async with sessionmaker() as db:
            db.add(TradeEvent(
                event_type=f"order_action_{action_type.lower()}",
                severity="info" if success else "error",
                description=error_message[:500] if error_message else None,
                payload={
                    "action_type": action_type,
                    "request_payload": request_payload,
                    "response_payload": response_payload,
                    "success": success,
                },
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


# ---- Structure-level endpoints (Step 4 phase 2) -------------------------

class SubmitStructureBody(BaseModel):
    structure_id: int = Field(gt=0)


class RollbackStructureBody(BaseModel):
    structure_id: int = Field(gt=0)


class HedgeOrderBody(BaseModel):
    hedge_order_id: int = Field(gt=0)
    front_month_expiry: str | None = Field(None, pattern=r"^\d{6,8}$")
    limit_price: float | None = Field(None, gt=0)


@router.post("/structure/submit")
async def submit_structure(
    body: SubmitStructureBody,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Live-submit all entry orders of a previously-persisted structure.

    Called by ``api.routers.trade.submit_preview`` when ``execution_mode='live'``.
    The structure + structure_orders rows are already in the DB (state='pending').
    """
    try:
        return await submit_structure_live(
            sessionmaker_factory=sm, executor=ex,
            structure_id=body.structure_id,
        )
    except LiveSubmitError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/structure/rollback")
async def rollback_structure(
    body: RollbackStructureBody,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Cancel + unwind a structure (called by api after a rejection or
    user-triggered abort)."""
    try:
        return await run_rollback(
            sessionmaker_factory=sm, executor=ex, structure_id=body.structure_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/hedge")
async def submit_hedge(
    body: HedgeOrderBody,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Submit a delta-hedge HedgeOrder row (state='pending') as an EUR FUT
    LMT order. Position-monitor calls this after creating the row."""
    try:
        return await submit_hedge_order(
            sessionmaker_factory=sm, executor=ex,
            hedge_order_id=body.hedge_order_id,
            front_month_expiry=body.front_month_expiry,
            limit_price=body.limit_price,
        )
    except HedgeSubmitError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


app.include_router(router)
