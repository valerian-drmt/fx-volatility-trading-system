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

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import require_write

router = APIRouter(prefix="/api/v1", tags=["orders"])

EXECUTION_URL = os.getenv("EXECUTION_URL", "http://execution-engine:8001")
TIMEOUT_S = 10.0


async def _forward(method: str, path: str, json: dict | None = None) -> dict[str, Any]:
    url = f"{EXECUTION_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.request(method, url, json=json)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"execution-engine unreachable: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()


@router.get("/orders")
async def list_orders(_: Request) -> dict[str, Any]:
    return await _forward("GET", "/internal/orders")


@router.post("/orders", dependencies=[Depends(require_write)])
async def place_order(body: dict, _: Request) -> dict[str, Any]:
    return await _forward("POST", "/internal/orders", json=body)


@router.delete("/orders/{order_id}", dependencies=[Depends(require_write)])
async def cancel_order(order_id: int, _: Request) -> dict[str, Any]:
    return await _forward("DELETE", f"/internal/orders/{order_id}")


@router.get("/exec/positions")
async def live_positions(_: Request) -> dict[str, Any]:
    return await _forward("GET", "/internal/positions")


@router.post("/exec/positions/{con_id}/close", dependencies=[Depends(require_write)])
async def close_position(con_id: int, body: dict, _: Request) -> dict[str, Any]:
    return await _forward("POST", f"/internal/positions/{con_id}/close", json=body)
