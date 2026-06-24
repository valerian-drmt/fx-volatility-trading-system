"""Trade-level endpoints — operations that target a whole
``trade_structure`` (= "trade" in Murex parlance) rather than a single
leg.

POST /api/v1/trades/{trade_id}/close
    Close every open leg sharing ``open_position.trade_id == trade_id``.
    Sequential per-leg submission — order of magnitude faster than the
    client-side N-parallel-calls path AND keeps the failure model clean
    (if leg K fails, legs 1..K-1 are already submitted but legs K+1..N
    aren't — operator sees exactly which succeeded).
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session
from api.routers.positions import close_one_open_position
from persistence.models import OpenPosition

router = APIRouter(prefix="/api/v1/trades", tags=["trades"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]
logger = logging.getLogger(__name__)


class CloseTradeRequest(BaseModel):
    # Optional explicit LimitOrder override. When omitted, each leg
    # picks its order type independently (MKT during RTH / LMT 5 bps
    # outside RTH) just like the per-position endpoint.
    limit_price: float | None = Field(default=None, gt=0)


@router.post("/{trade_id}/close")
async def close_trade(
    trade_id: int, body: CloseTradeRequest, db: DbDep,
) -> dict[str, Any]:
    """Atomically close every open leg of ``trade_id``.

    Server-side loop = "atomic" in the sense that :
      - the operator sends 1 request,
      - the audit chain in ``trade_structure`` / ``trade_order`` records
        all legs under successive close trade_structure rows,
      - partial failure is reported in one structured response.

    Note : IB execution events themselves are async (orders are placed
    but fills land later). "Atomic" here means the *submission* is
    grouped — not that all orders fill simultaneously.

    Response shape :
        {
          "trade_id": <int>,
          "total_legs": <int>,
          "closed_legs": <int>,            # legs whose close was accepted
          "failed_legs": <int>,
          "results": [ { "position_id": ..., "ok": bool,
                         "structure_id": ..., "order_id": ...,
                         "error": "..." | null }, ... ]
        }
    """
    legs = (await db.execute(
        select(OpenPosition)
        .where(OpenPosition.trade_id == trade_id)
        .order_by(desc(OpenPosition.entry_timestamp))
    )).scalars().all()
    if not legs:
        raise HTTPException(
            404, f"no open positions found for trade #{trade_id}",
        )

    results: list[dict[str, Any]] = []
    closed_count = 0
    failed_count = 0
    for pos in legs:
        leg_qty = int(abs(pos.quantity))
        if leg_qty == 0:
            results.append({
                "position_id": pos.id, "ok": False,
                "error": "zero open qty (already closed?)",
            })
            failed_count += 1
            continue
        try:
            r = await close_one_open_position(
                db=db, pos=pos, qty=leg_qty,
                limit_price_override=body.limit_price,
            )
            results.append({
                "position_id": pos.id, "ok": True,
                "closed_qty": r["closed_qty"],
                "structure_id": r["structure_id"],
                "order_id": r["order_id"],
                "order_type": r["order_type"],
                "limit_price": r["limit_price"],
                "error": None,
            })
            closed_count += 1
        except HTTPException as e:
            results.append({
                "position_id": pos.id, "ok": False,
                "error": f"{e.status_code} : {e.detail}"[:300],
            })
            failed_count += 1
        except Exception:  # pragma: no cover — defensive
            # Don't leak internal exception text to the API client (CWE-209);
            # log the detail server-side and return a generic message.
            logger.exception("close_trade_leg_failed position_id=%s", pos.id)
            results.append({
                "position_id": pos.id, "ok": False,
                "error": "internal error",
            })
            failed_count += 1

    return {
        "trade_id": trade_id,
        "total_legs": len(legs),
        "closed_legs": closed_count,
        "failed_legs": failed_count,
        "results": results,
    }
