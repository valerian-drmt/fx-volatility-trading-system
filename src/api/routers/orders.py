"""Orders router — place / cancel / list + positions live + close.

Endpoints (R9 sandbox) — préfixés `/exec/` pour éviter la collision avec
`portfolio_router /positions/{id}` qui catcherait `/positions/live` :
  - GET    /api/v1/orders                            → openOrders côté IB
  - POST   /api/v1/orders                             → place a LimitOrder
  - DELETE /api/v1/orders/{id}                        → cancel an open order
  - GET    /api/v1/exec/positions                     → positions live IB
  - POST   /api/v1/exec/positions/{con_id}/close      → reverse limit order

Toutes les routes renvoient 503 si la connexion IB est DOWN.

⚠ R9 sandbox — pas de feature flag prod, pas d'auth, pas de cap qty.
À durcir avant déploiement EC2.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.services.order_executor import OrderExecutor, OrderExecutorUnavailable, OrderRequest

router = APIRouter(prefix="/api/v1", tags=["orders"])


def _executor(request: Request) -> OrderExecutor:
    ex = getattr(request.app.state, "order_executor", None)
    if ex is None or not ex.is_connected():
        # Best-effort reconnect — utile si Gateway est revenu après le boot api.
        if ex is not None:
            import asyncio
            try:
                asyncio.get_event_loop().create_task(ex.connect(timeout=2.0))
            except Exception:
                pass
        raise HTTPException(status_code=503, detail="IB Gateway not connected (api → ib-gateway)")
    return ex


class PlaceOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    sec_type: Literal["FUT", "FOP"]
    side: Literal["BUY", "SELL"]
    qty: int = Field(gt=0, le=1000)
    limit_price: float = Field(gt=0)
    expiry: str | None = Field(None, pattern=r"^\d{8}$")  # YYYYMMDD
    strike: float | None = Field(None, gt=0)
    right: Literal["C", "P"] | None = None
    exchange: str = "CME"
    currency: str = "USD"
    trading_class: str | None = None


class ClosePositionRequest(BaseModel):
    limit_price: float = Field(gt=0)


@router.get("/orders")
async def list_orders(request: Request) -> dict[str, Any]:
    ex = _executor(request)
    try:
        orders = await ex.list_open_orders()
    except OrderExecutorUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"orders": orders, "count": len(orders)}


@router.post("/orders")
async def place_order(body: PlaceOrderRequest, request: Request) -> dict[str, Any]:
    ex = _executor(request)
    if body.sec_type == "FOP":
        if body.expiry is None or body.strike is None or body.right is None:
            raise HTTPException(status_code=400, detail="FOP requires expiry, strike, right")
    if body.sec_type == "FUT" and body.expiry is None:
        raise HTTPException(status_code=400, detail="FUT requires expiry")

    req = OrderRequest(
        symbol=body.symbol,
        sec_type=body.sec_type,
        side=body.side,
        qty=body.qty,
        limit_price=body.limit_price,
        expiry=body.expiry,
        strike=body.strike,
        right=body.right,
        exchange=body.exchange,
        currency=body.currency,
        trading_class=body.trading_class,
    )
    try:
        result = await ex.place_order(req)
    except OrderExecutorUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: int, request: Request) -> dict[str, Any]:
    ex = _executor(request)
    try:
        result = await ex.cancel_order(order_id)
    except OrderExecutorUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if result is None:
        raise HTTPException(status_code=404, detail=f"order {order_id} not in open trades")
    return result


@router.get("/exec/positions")
async def live_positions(request: Request) -> dict[str, Any]:
    ex = _executor(request)
    try:
        positions = await ex.list_positions()
    except OrderExecutorUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"positions": positions, "count": len(positions)}


@router.post("/exec/positions/{con_id}/close")
async def close_position(con_id: int, body: ClosePositionRequest, request: Request) -> dict[str, Any]:
    ex = _executor(request)
    try:
        result = await ex.close_position(con_id=con_id, limit_price=body.limit_price)
    except OrderExecutorUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result
