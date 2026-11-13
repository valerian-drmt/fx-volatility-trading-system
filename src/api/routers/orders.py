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
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_write
from api.config import get_settings
from api.dependencies import get_db_session, get_redis_client_or_none
from api.orchestration.pretrade_gate import run_spot_order_gate
from api.schemas import route_models as rm
from api.schemas.orders import ClosePositionBody, PlaceOrderBody
from persistence.models import TradeEvent
from shared.internal_auth import internal_headers

router = APIRouter(prefix="/api/v1", tags=["orders"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]
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


@router.get("/orders", response_model=rm.OrdersList, response_model_exclude_unset=True)
async def list_orders(_: Request) -> dict[str, Any]:
    """Open orders as the execution engine holds them (forwarded to its `/internal/orders`)."""
    return await _forward("GET", "/internal/orders")


@router.post("/orders", response_model=rm.IbOrder, response_model_exclude_unset=True, dependencies=[Depends(require_write)])
async def place_order(body: PlaceOrderBody, _: Request, db: DbDep) -> dict[str, Any]:
    """Place one order through the execution engine. Requires the trader cookie.

    What applies (#491):

    * a **spot** order passes the pre-trade checks here: kill switch, book
      priced, delta and notional limits on the book after the order (an order
      that reduces the book's delta is never refused by the delta limit);
    * a **future or option** order can only reduce a position the book holds
      (the engine refuses anything else): a new position is opened through
      the order ticket, where the full pre-trade gate is;
    * the engine's kill switch and ready gate apply to both.
    """
    if body.sec_type == "CASH":
        gate = await run_spot_order_gate(db, get_redis_client_or_none(), side=body.side, qty_eur=float(body.qty))
        if not gate.passed:
            db.add(TradeEvent(
                structure_id=None, event_type="submission_blocked", severity="warning",
                description=("spot order gate: " + ", ".join(gate.failures))[:450],
                payload={"failures": gate.failures, "details": gate.details, "order": body.model_dump()},
            ))
            await db.commit()
            raise HTTPException(400, detail={
                "error": "pretrade_gate_failed", "failures": gate.failures, "details": gate.details,
            })
    return await _forward("POST", "/internal/orders", json=body.model_dump())


@router.delete("/orders/{order_id}", response_model=rm.IbOrder, response_model_exclude_unset=True, dependencies=[Depends(require_write)])
async def cancel_order(order_id: int, _: Request) -> dict[str, Any]:
    """Cancel one working order at IB through the execution engine."""
    return await _forward("DELETE", f"/internal/orders/{order_id}")


@router.get("/exec/positions", response_model=rm.IbPositionsList, response_model_exclude_unset=True)
async def live_positions(_: Request) -> dict[str, Any]:
    """Positions as IB reports them right now (the broker's view, not the book)."""
    return await _forward("GET", "/internal/positions")


@router.post("/exec/positions/{con_id}/close", response_model=rm.IbOrder, response_model_exclude_unset=True, dependencies=[Depends(require_write)])
async def close_position(con_id: int, body: ClosePositionBody, _: Request) -> dict[str, Any]:
    """Switched off (#491). This route closed a whole position by contract id
    with no row in the book, no guard against a second close and no trace on
    the panels. Close through ``POST /positions/{id}/close``, which books the
    close, reserves the leg and shows the position as closing."""
    del body
    raise HTTPException(410, detail=(
        f"closing by contract id ({con_id}) is switched off: use POST /api/v1/positions/{{id}}/close"
    ))
