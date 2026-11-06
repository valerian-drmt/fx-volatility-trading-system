"""Kill-switch enforcement in the execution engine (#315, audit 04#13).

Every route that can create an order at IB depends on :func:`require_not_halted`.
The flag is read from Postgres (``persistence.halt``, the authority) on each
call; a read failure is a refusal (503), never a pass. ``cancel_all_open``
is what ``POST /internal/halt`` runs: every open trade in this IB session is
cancelled, plus IB's global cancel when the client exposes it.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.halt import is_halted

logger = logging.getLogger(__name__)

HALTED_STATUS = 423  # Locked: the resource exists, writes are refused until resumed


async def check_not_halted(sm: async_sessionmaker[AsyncSession]) -> None:
    """Raise 423 when halted, 503 when the flag cannot be read."""
    try:
        async with sm() as db:
            halted = await is_halted(db)
    except Exception as e:  # DB down: no way to know -> no new orders
        logger.exception("halt_flag_unreadable")
        raise HTTPException(status_code=503, detail="halt flag unreadable — refusing to place") from e
    if halted:
        raise HTTPException(status_code=HALTED_STATUS, detail="trading halted (kill switch) — no new orders")


async def require_not_halted(request: Request) -> None:
    await check_not_halted(request.app.state.sessionmaker)


async def cancel_all_open(ib: Any) -> dict[str, int]:
    """Cancel every working order of this session. Best effort per order; the
    global cancel covers orders this client cannot see."""
    n_cancel = 0
    n_fail = 0
    for t in list(getattr(ib, "openTrades", lambda: [])()):
        try:
            ib.cancelOrder(t.order)
            n_cancel += 1
        except Exception:
            n_fail += 1
            logger.exception("halt_cancel_failed order_id=%s", getattr(t.order, "orderId", None))
    global_cancel = False
    if hasattr(ib, "reqGlobalCancel"):
        try:
            ib.reqGlobalCancel()
            global_cancel = True
        except Exception:
            logger.exception("halt_global_cancel_failed")
    return {"cancelled": n_cancel, "failed": n_fail, "global_cancel": int(global_cancel)}
