"""Orders router — pure proxy vers le container `execution-engine`.

L'api ne touche plus IB directement (cf. R9 sandbox split). Toutes les
mutations sont forwardées via httpx vers http://execution-engine:8001.

Per-request identifiers (order_id, con_id) travel as **query params**, not
in the forwarded URL path — the path is always a string literal. So no
client-provided value ever reaches the request URL's host or path (no SSRF);
httpx url-encodes the params.

Endpoints côté client :
  - GET    /api/v1/orders                     → GET    /internal/orders
  - POST   /api/v1/orders                     → POST   /internal/orders
  - DELETE /api/v1/orders/{id}                → DELETE /internal/orders?order_id={id}
  - GET    /api/v1/exec/positions             → GET    /internal/positions
  - POST   /api/v1/exec/positions/{id}/close  → POST   /internal/positions/close?con_id={id}
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


async def _forward(
    method: str,
    path: str,
    *,
    params: dict[str, int] | None = None,
    json: dict | None = None,
) -> dict[str, Any]:
    # ``path`` is always a string literal (see callers below). Identifiers go
    # through ``params`` (httpx url-encodes them), so no caller-supplied value
    # ever lands in the request URL's host or path.
    url = f"{EXECUTION_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.request(method, url, params=params, json=json)
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
    if order_id < 1:
        raise HTTPException(status_code=422, detail="order_id must be a positive integer")
    return await _forward("DELETE", "/internal/orders", params={"order_id": order_id})


@router.get("/exec/positions")
async def live_positions(_: Request) -> dict[str, Any]:
    return await _forward("GET", "/internal/positions")


@router.post("/exec/positions/{con_id}/close", dependencies=[Depends(require_write)])
async def close_position(con_id: int, body: dict, _: Request) -> dict[str, Any]:
    if con_id < 1:
        raise HTTPException(status_code=422, detail="con_id must be a positive integer")
    return await _forward("POST", "/internal/positions/close", params={"con_id": con_id}, json=body)
