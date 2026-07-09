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
import os
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_write
from api.dependencies import get_db_session
from api.routers.positions import close_one_open_position
from core.execution.reaper_policy import TERMINAL_STATES
from persistence.models import OpenPosition, StructureOrder, TradeStructure

_EXECUTION_URL = os.getenv("EXECUTION_URL", "http://execution-engine:8001")
# Non-terminal order states an operator can cancel from the blotter.
_CANCELLABLE_STATES = ("pending", "submitted", "acknowledged", "partially_filled")

router = APIRouter(prefix="/api/v1/trades", tags=["trades"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]
logger = logging.getLogger(__name__)


def plan_trade_close(
    entry_legs: list[tuple[int, str | None, int]],
    mirror_qty: dict[str, int],
) -> tuple[list[tuple[str, int, int]], list[tuple[int, str]]]:
    """Plan a trade-level close from the trade's OWN entry legs (pure/testable).

    ``entry_legs`` = ``[(entry_order_id, ib_local_symbol, qty_filled), …]``.
    ``mirror_qty`` = ``{localSymbol: abs(netted IB qty)}``.

    Each leg closes its own filled qty of its contract, **capped at the live
    (netted) mirror qty** — so a sibling trade holding the SAME contract can
    never be over-closed (the D2 netting defect). Returns ``(plans, skips)``
    where ``plan = (local_symbol, qty, entry_order_id)`` and
    ``skip = (entry_order_id, reason)``.
    """
    plans: list[tuple[str, int, int]] = []
    skips: list[tuple[int, str]] = []
    for order_id, sym, qty_filled in entry_legs:
        if not sym:
            skips.append((order_id, "leg has no IB contract (unfilled)"))
            continue
        avail = mirror_qty.get(sym)
        if avail is None:
            skips.append((order_id, f"no live mirror position for {sym}"))
            continue
        qty = min(int(qty_filled or 0), int(avail))
        if qty <= 0:
            skips.append((order_id, "zero qty (already closed?)"))
            continue
        plans.append((sym, qty, order_id))
    return plans, skips


class CloseTradeRequest(BaseModel):
    # Optional explicit LimitOrder override. When omitted, each leg
    # picks its order type independently (MKT during RTH / LMT 5 bps
    # outside RTH) just like the per-position endpoint.
    limit_price: float | None = Field(default=None, gt=0)


@router.post("/{trade_id}/close", dependencies=[Depends(require_write)])
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
    # Close by the trade's OWN filled entry legs — NOT the netted mirror's
    # back-attributed OpenPosition.trade_id. IB nets by contract, and the
    # attribution "most-recent structure wins", so two trades sharing a contract
    # collapse to one mirror row stamped with the newer trade — closing by that
    # id over-closes the sibling. Targeting the trade's own trade_order legs
    # (and capping each at its filled qty) closes exactly this trade's lots.
    entry_legs = (await db.execute(
        select(StructureOrder)
        .where(StructureOrder.structure_id == trade_id)
        .where(StructureOrder.order_role == "entry")
        .where(StructureOrder.qty_filled > 0)
        .order_by(StructureOrder.leg_idx)
    )).scalars().all()
    if not entry_legs:
        raise HTTPException(404, f"no filled entry legs for trade #{trade_id}")

    # Live mirror keyed by IB localSymbol (one netted row per contract) for the
    # mark/contract + the available-qty cap.
    mirror = (await db.execute(select(OpenPosition))).scalars().all()
    pos_by_symbol: dict[str, OpenPosition] = {}
    for p in mirror:
        if p.structure and p.structure not in pos_by_symbol:
            pos_by_symbol[p.structure] = p
    mirror_qty = {sym: int(abs(p.quantity or 0)) for sym, p in pos_by_symbol.items()}

    plans, skips = plan_trade_close(
        [(int(leg.id), leg.ib_local_symbol, int(leg.qty_filled or 0)) for leg in entry_legs],
        mirror_qty,
    )

    results: list[dict[str, Any]] = []
    closed_count = 0
    failed_count = len(skips)
    for order_id, reason in skips:
        results.append({"entry_order_id": order_id, "ok": False, "error": reason})

    for sym, qty, entry_order_id in plans:
        pos = pos_by_symbol[sym]
        try:
            r = await close_one_open_position(
                db=db, pos=pos, qty=qty,
                limit_price_override=body.limit_price,
                entry_order_id_override=entry_order_id,
            )
            results.append({
                "position_id": pos.id, "entry_order_id": entry_order_id, "ok": True,
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
                "entry_order_id": entry_order_id, "ok": False,
                "error": f"{e.status_code} : {e.detail}"[:300],
            })
            failed_count += 1
        except Exception:  # pragma: no cover — defensive
            # Don't leak internal exception text to the API client (CWE-209);
            # log the detail server-side and return a generic message.
            logger.exception("close_trade_leg_failed entry_order_id=%s", entry_order_id)
            results.append({
                "entry_order_id": entry_order_id, "ok": False,
                "error": "internal error",
            })
            failed_count += 1

    return {
        "trade_id": trade_id,
        "total_legs": len(entry_legs),
        "closed_legs": closed_count,
        "failed_legs": failed_count,
        "results": results,
    }


def plan_structure_terminal_state(order_states: list[str]) -> str | None:
    """Terminal ``trade_structure`` state from its orders' states, or ``None`` if
    any order is still in flight (pure/testable). All filled → ``fully_filled`` ;
    none filled → ``fully_failed`` ; mixed → ``partial_fail``."""
    if not order_states or any(s not in TERMINAL_STATES for s in order_states):
        return None
    filled = sum(1 for s in order_states if s == "filled")
    if filled == len(order_states):
        return "fully_filled"
    return "fully_failed" if filled == 0 else "partial_fail"


@router.post("/{trade_id}/cancel", dependencies=[Depends(require_write)])
async def cancel_trade(trade_id: int, db: DbDep) -> dict[str, Any]:
    """Cancel every non-terminal order of ``trade_id`` at IB, then terminalise the
    structure. Frees a stuck 'submitted' trade — a resting wing that won't fill,
    or a DAY order IB already dropped. Idempotent : an order IB no longer holds
    (404 from the engine) is treated as already gone and flipped to ``cancelled``.
    """
    orders = (await db.execute(
        select(StructureOrder)
        .where(StructureOrder.structure_id == trade_id)
        .where(StructureOrder.state.in_(_CANCELLABLE_STATES))
        .order_by(StructureOrder.leg_idx)
    )).scalars().all()
    if not orders:
        raise HTTPException(404, f"no cancellable orders for trade #{trade_id}")

    now = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    cancelled = 0
    failed = 0
    for o in orders:
        try:
            if o.ib_order_id:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.delete(
                        f"{_EXECUTION_URL}/internal/orders/{int(o.ib_order_id)}"
                    )
                # 404 = the order isn't live at IB anymore (already gone) → OK.
                if r.status_code >= 400 and r.status_code != 404:
                    raise HTTPException(r.status_code, str(r.text)[:300])
            o.state = "cancelled"
            o.state_updated_at = now
            results.append({
                "order_id": o.id, "ib_order_id": o.ib_order_id, "ok": True,
            })
            cancelled += 1
        except HTTPException as e:
            results.append({
                "order_id": o.id, "ok": False,
                "error": f"{e.status_code} : {e.detail}"[:300],
            })
            failed += 1
        except httpx.HTTPError as e:
            results.append({
                "order_id": o.id, "ok": False,
                "error": f"execution-engine unreachable: {e}"[:300],
            })
            failed += 1

    # Terminalise the structure once all its orders are terminal (the missing
    # transition — a structure only reached 'fully_filled' when ALL legs filled).
    all_states = (await db.execute(
        select(StructureOrder.state).where(StructureOrder.structure_id == trade_id)
    )).scalars().all()
    new_state = plan_structure_terminal_state(list(all_states))
    if new_state is not None:
        struct = await db.get(TradeStructure, trade_id)
        if struct is not None and struct.state != new_state:
            struct.state = new_state
            struct.state_updated_at = now
    await db.commit()
    return {
        "trade_id": trade_id, "cancelled": cancelled, "failed": failed,
        "structure_state": new_state, "results": results,
    }
