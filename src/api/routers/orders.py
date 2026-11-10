"""Orders router — pure proxy to the `execution-engine` container.

The api never touches IB directly: every mutation is forwarded
via httpx to http://execution-engine:8001.

Endpoints unchanged from the client's point of view:
  - GET    /api/v1/orders               → forward GET  /internal/orders
  - POST   /api/v1/orders                → forward POST /internal/orders
  - DELETE /api/v1/orders/{id}           → forward DELETE /internal/orders/{id}
  - GET    /api/v1/exec/positions        → forward GET  /internal/positions
  - POST   /api/v1/exec/positions/{id}/close
                                          → forward POST /internal/positions/{id}/close
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import require_write
from api.config import get_settings
from api.schemas.orders import ClosePositionBody, PlaceOrderBody
from shared.internal_auth import internal_headers

router = APIRouter(prefix="/api/v1", tags=["orders"])
logger = logging.getLogger(__name__)

EXECUTION_URL = get_settings().EXECUTION_URL
TIMEOUT_S = 10.0


async def _forward(method: str, path: str, json: dict | None = None) -> dict[str, Any]:
    url = f"{EXECUTION_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.request(method, url, json=json, headers=internal_headers())
    except httpx.HTTPError as e:
        # Static detail: the exception text names the internal URL (#297).
        logger.warning(
            "execution_forward_failed method=%s path=%s exc_type=%s",
            method, path, type(e).__name__,
        )
        raise HTTPException(status_code=503, detail="execution-engine unreachable") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()


@router.get("/orders")
async def list_orders(_: Request) -> dict[str, Any]:
    """Open orders as the execution engine holds them (forwarded to its `/internal/orders`)."""
    return await _forward("GET", "/internal/orders")


@router.post("/orders", dependencies=[Depends(require_write)])
async def place_order(body: PlaceOrderBody, _: Request) -> dict[str, Any]:
    """Place one order through the execution engine. Requires the trader cookie; the engine's pre-trade gate and the kill switch apply."""
    return await _forward("POST", "/internal/orders", json=body.model_dump())


@router.delete("/orders/{order_id}", dependencies=[Depends(require_write)])
async def cancel_order(order_id: int, _: Request) -> dict[str, Any]:
    """Cancel one working order at IB through the execution engine."""
    return await _forward("DELETE", f"/internal/orders/{order_id}")


@router.get("/exec/positions")
async def live_positions(_: Request) -> dict[str, Any]:
    """Positions as IB reports them right now (the broker's view, not the book)."""
    return await _forward("GET", "/internal/positions")


@router.post("/exec/positions/{con_id}/close", dependencies=[Depends(require_write)])
async def close_position(con_id: int, body: ClosePositionBody, _: Request) -> dict[str, Any]:
    """Close one IB position (by contract id) through the execution engine."""
    return await _forward("POST", f"/internal/positions/{con_id}/close", json=body.model_dump())
