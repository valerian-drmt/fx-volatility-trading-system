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
from pydantic import BaseModel, Field, model_validator
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.fills_handler import attach_fill_handlers
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
    trade_to_dict,
)
from engines.execution.order_reconciler import reconcile_loop, reconcile_stuck_orders
from engines.execution.position_sync import position_sync_loop, sync_positions_from_ib
from engines.execution.reaper import reap_stale_orders, reaper_loop
from engines.execution.reconciler import reconcile_positions, reconcile_positions_loop
from engines.execution.redis_state import set_client as set_redis_client
from engines.execution.rollback_runner import run_rollback
from persistence.db import get_sessionmaker
from persistence.models import TradeEvent
from persistence.projection import rebuild_all
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

SYNC_INTERVAL_S = float(os.getenv("SYNC_INTERVAL_S", "1.0"))
HEARTBEAT_INTERVAL_S = float(os.getenv("HEARTBEAT_INTERVAL_S", "10.0"))
STUCK_WATCH_INTERVAL_S = float(os.getenv("STUCK_WATCH_INTERVAL_S", "60.0"))
STUCK_AFTER_S = float(os.getenv("STUCK_AFTER_S", "600.0"))
RECONCILE_INTERVAL_S = float(os.getenv("RECONCILE_INTERVAL_S", "60.0"))

# Combo (BAG) execution : place combo-eligible option structures as a single IB
# BAG so multi-leg trades fill all-or-nothing (no naked half-fill). OFF by default
# — the per-leg path stays the tested fallback until validated on paper.
EXECUTION_USE_COMBO = os.getenv("EXECUTION_USE_COMBO", "0").lower() in ("1", "true", "yes")


def _scrub(value: Any) -> str:
    """Neutralise CR/LF in request-derived values before logging (CWE-117),
    so a crafted payload can't forge extra log lines in Loki."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


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

    # Seed the forward projection (leg_position) from the fill log so the book is
    # current before the first fill event this session (I3). Best-effort — never
    # block startup on it.
    try:
        async with sm() as db:
            n_legs = await rebuild_all(db)
            await db.commit()
        logger.info("leg_projection_seeded legs=%s", n_legs)
    except Exception:
        logger.exception("leg_projection_seed_failed")

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
    # Backfill stuck combo legs from the live IB positions (gateway truth). Combo
    # BAG fills report on the combo conId → the per-leg router can miss them ; this
    # flips a submitted leg to filled once IB actually holds that contract.
    reconcile_task = asyncio.create_task(
        reconcile_loop(sm, interval_s=RECONCILE_INTERVAL_S),
        name="order_reconcile_loop",
    )
    # Liveness (I2 / D1) : terminalise stale orders IB does not hold to `expired`.
    # Guarded by account_is_reporting ; the filled backfill stays in reconcile_loop.
    reaper_task = asyncio.create_task(
        reaper_loop(sm, executor),
        name="order_reaper_loop",
    )
    # Reconciliation (I4 / D3) : materialise book (leg_position) vs broker (mirror)
    # breaks. Guarded by account_is_reporting. Setpoint = break 0.
    reconcile_pos_task = asyncio.create_task(
        reconcile_positions_loop(sm, executor),
        name="position_reconcile_loop",
    )
    logger.info(
        "execution_startup ib_connected=%s sync_interval=%.1fs heartbeat=%.1fs",
        executor.is_connected(), SYNC_INTERVAL_S, HEARTBEAT_INTERVAL_S,
    )
    try:
        yield
    finally:
        for task in (sync_task, heartbeat_task, stuck_task, reconcile_task, reaper_task, reconcile_pos_task):
            task.cancel()
        for task in (sync_task, heartbeat_task, stuck_task, reconcile_task, reaper_task, reconcile_pos_task):
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


@app.middleware("http")
async def _trace_middleware(request: Request, call_next):
    """Bind the caller's correlation id (X-Trace-ID) for this request so IB order
    placement / fills logged here share the API's trace_id — one grep spans both
    services. Mints one if the caller didn't send it (direct /internal call)."""
    from shared.trace import TRACE_HEADER, bind_trace_id, clear_trace_id, new_trace_id
    trace_id = request.headers.get(TRACE_HEADER) or new_trace_id()
    bind_trace_id(trace_id)
    try:
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        return response
    finally:
        clear_trace_id()


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
    sec_type: Literal["FUT", "FOP", "CASH"]
    side: Literal["BUY", "SELL"]
    # qty is CONTRACTS for FUT/FOP but base-currency NOTIONAL for CASH
    # (spot EUR.USD : qty=100_000 = 100k EUR) — hence the per-secType bound
    # in the validator below instead of a single ``le=``.
    qty: int = Field(gt=0)
    # None ⇒ MarketOrder. Only CASH may omit it (spot cash management);
    # FUT/FOP keep the mandatory limit (IB's option price-cap hangs markets).
    limit_price: float | None = Field(None, gt=0)
    expiry: str | None = Field(None, pattern=r"^\d{8}$")
    strike: float | None = Field(None, gt=0)
    right: Literal["C", "P"] | None = None
    exchange: str = "CME"
    currency: str = "USD"
    trading_class: str | None = None

    @model_validator(mode="after")
    def _per_sec_type_bounds(self) -> PlaceOrderBody:
        if self.sec_type == "CASH":
            if self.qty > 5_000_000:
                raise ValueError("spot qty must be <= 5,000,000 (base-ccy notional)")
        else:
            if self.qty > 1000:
                raise ValueError("qty must be <= 1000 contracts")
            if self.limit_price is None:
                raise ValueError("limit_price is required for FUT/FOP orders")
        return self


class ClosePositionBody(BaseModel):
    limit_price: float = Field(gt=0)


class ClosePositionBySymbolBody(BaseModel):
    local_symbol: str = Field(min_length=1, max_length=20)
    qty: int | None = Field(default=None, gt=0)  # None = close full open qty
    # limit_price=None ⇒ MarketOrder (default for closes). Explicit value
    # ⇒ LimitOrder (operator override).
    limit_price: float | None = Field(default=None, gt=0)
    # Optional DB ``trade_order.id`` so the execution-engine can wire
    # fills_handler callbacks (status / execution events → DB updates).
    # When omitted, the close still goes through to IB but qty_filled
    # won't be reflected in the DB row.
    db_order_id: int | None = Field(default=None, gt=0)


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


@router.get("/trades")
async def list_trades(ex: Annotated[OrderExecutor, Depends(_executor_dep)]) -> dict[str, Any]:
    """All trades in the current IB session (open + done + rejected).

    Diagnostic endpoint : when an order disappears from ``/orders`` it
    can mean filled, cancelled or rejected. Each row here carries
    ``status`` + ``last_log`` which contains IB's reject reason.
    """
    trades = await ex.list_all_trades()
    return {"trades": trades, "count": len(trades)}


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


@router.post("/positions/close-by-symbol")
async def close_position_by_symbol(
    body: ClosePositionBySymbolBody,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Partial / full close keyed on IB ``localSymbol`` (no con_id needed).

    Called by the API ``POST /api/v1/positions/{id}/close`` so the
    operator can close from the DB ``position.id`` without round-tripping
    to IB to resolve a ``conId``.

    After submitting the reverse LimitOrder, immediately runs one
    ``sync_positions_from_ib`` cycle so a fill that lands instantly
    (paper / marketable orders) is reflected on the DB row without
    waiting for the next 30 s background tick. If IB hasn't filled
    yet, the row is unchanged ; the background loop continues to
    poll and will pick up the fill when it occurs.
    """
    payload = {
        "local_symbol": body.local_symbol,
        "qty": body.qty,
        "limit_price": body.limit_price,
        "db_order_id": body.db_order_id,
    }
    try:
        trade = await ex.close_position_by_symbol(
            local_symbol=body.local_symbol,
            qty=body.qty,
            limit_price=body.limit_price,
        )
        # Wire fills_handler so IB status / execution events flow back
        # to the DB ``trade_order`` row — keeps qty_filled / state in sync.
        if body.db_order_id is not None:
            attach_fill_handlers(
                trade=trade,
                order_id=body.db_order_id,
                sessionmaker_factory=sm,
            )
        result = trade_to_dict(trade)
        await _log_event(sm, action_type="CLOSE_POSITION_BY_SYMBOL",
                         request_payload=payload,
                         response_payload=result, success=True)
    except (OrderExecutorUnavailable, ValueError) as e:
        await _log_event(sm, action_type="CLOSE_POSITION_BY_SYMBOL",
                         request_payload=payload, response_payload=None,
                         success=False, error_message=str(e)[:500])
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Best-effort immediate sync — never blocks the close response on
    # failure. Errors are logged but don't fail the caller : the order
    # is already in flight at IB.
    sync_summary: dict[str, Any] = {"synced": False}
    try:
        async with sm() as db:
            sync_summary = await sync_positions_from_ib(db, ex)
            sync_summary["synced"] = True
    except Exception:
        # Full detail goes to the engine log only — exception text in the
        # response would leak internals to the API client (CWE-209).
        logger.exception("post_close_sync_failed local_symbol=%s", _scrub(body.local_symbol))
        sync_summary = {"synced": False, "error": "position sync failed — see engine logs"}
    return {**result, "post_close_sync": sync_summary}


@router.post("/positions/sync")
async def trigger_position_sync(
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Run one ``sync_positions_from_ib`` cycle on demand.

    Convenience for the dev UI / operator. The same function runs
    every 30 s in the background, so calling this is only useful
    when you need a fresh snapshot right now (e.g. after a manual
    close that hasn't yet bubbled through the background loop).
    """
    try:
        async with sm() as db:
            result = await sync_positions_from_ib(db, ex)
        await _log_event(sm, action_type="SYNC_POSITIONS",
                         request_payload={}, response_payload=result, success=True)
        return result
    except OrderExecutorUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        await _log_event(sm, action_type="SYNC_POSITIONS",
                         request_payload={}, response_payload=None,
                         success=False, error_message=str(e)[:500])
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---- Structure-level endpoints (Step 4 phase 2) -------------------------

class SubmitStructureBody(BaseModel):
    structure_id: int = Field(gt=0)


class RollbackStructureBody(BaseModel):
    structure_id: int = Field(gt=0)


class HedgeOrderBody(BaseModel):
    hedge_order_id: int = Field(gt=0)
    front_month_expiry: str | None = Field(None, pattern=r"^\d{6,8}$")
    limit_price: float | None = Field(None, gt=0)


@router.post("/reconcile")
async def reconcile_now(
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Run one reconciliation pass NOW (on top of the 60s loop) — flip stuck
    orders to filled where the IB gateway actually holds the position. Useful to
    catch up the backlog immediately after a deploy."""
    return await reconcile_stuck_orders(sm)


@router.post("/reap")
async def reap_now(
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Run one reaper pass NOW (on top of the 30s loop) — terminalise stale
    orders IB does not hold to `expired` (liveness / I2). Guarded by
    account_is_reporting, so a dead feed is a no-op."""
    return await reap_stale_orders(sm, ex)


@router.post("/reconcile-positions")
async def reconcile_positions_now(
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Run one position-reconciliation pass NOW (on top of the loop) —
    materialise book (leg_position) vs broker (mirror) breaks (I4). Guarded by
    account_is_reporting, so a dead feed is a no-op."""
    return await reconcile_positions(sm, ex)


@router.post("/structure/submit")
async def submit_structure(
    body: SubmitStructureBody,
    ex: Annotated[OrderExecutor, Depends(_executor_dep)],
    sm: Annotated[async_sessionmaker[AsyncSession], Depends(_sm_dep)],
) -> dict[str, Any]:
    """Live-submit all entry orders of a previously-persisted structure.

    Called by ``api.routers.trade.submit_preview`` when ``execution_mode='live'``.
    The structure + structure_orders rows are already in the DB (state='pending').
    All failures are logged with event=``live_submit_failed`` so Grafana /
    Loki picks them up (``{container="fxvol-execution"} |= "live_submit_failed"``).
    """
    logger.info(
        "live_submit_received structure_id=%s ib_connected=%s",
        _scrub(body.structure_id), ex.is_connected(),
    )
    try:
        result = await submit_structure_live(
            sessionmaker_factory=sm, executor=ex,
            structure_id=body.structure_id,
            use_combo=EXECUTION_USE_COMBO,
        )
        logger.info(
            "live_submit_ok structure_id=%s body=%s",
            _scrub(body.structure_id), _scrub(str(result)[:300]),
        )
        return result
    except LiveSubmitError as e:
        logger.error(
            "live_submit_failed structure_id=%s reason=%s",
            _scrub(body.structure_id), _scrub(str(e)[:300]),
        )
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        import traceback as _tb
        tb = _tb.format_exc()
        logger.error(
            "live_submit_failed structure_id=%s exc_type=%s exc=%s\n%s",
            _scrub(body.structure_id), type(e).__name__, _scrub(str(e)[:300]), tb,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "live_submit_unhandled_exception",
                "exception_type": type(e).__name__,
                "message": str(e)[:500],
                "traceback_tail": tb.strip().splitlines()[-5:],
            },
        ) from e


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
    LMT order. OpenPosition-monitor calls this after creating the row."""
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
