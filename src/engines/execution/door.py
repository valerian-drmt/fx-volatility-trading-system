"""The one door to the broker (#491).

Every order this engine sends goes through :func:`place_at_broker`; nothing
else in the engine calls ``ib.placeOrder``. The checks that hold for EVERY
order live here, so a new path cannot forget them:

* the kill switch (``persistence.halt``, read from Postgres on each call; a
  flag that cannot be read is a refusal);
* the ready gate: the book has been reconciled with IB on this connection.

They used to be dependencies of the engine's HTTP routes only. The package
hedge sent after a fill and its retry loop are not routes: they placed
futures with the kill switch on. The per-leg submit checked once for the
whole structure, so a halt set between two legs did not stop the second.

What is NOT here, on purpose (owner rule, 2026-11-13: the app is not the
trader): no limit, size or level computed by the code. The facts about the
market and the account, and the limits the trader typed, are judged before
the rows are written (``core.risk.pretrade``), where the answer can still be
shown on the ticket.

The door refuses with the same HTTP errors as the route guards (423 halted,
503 not ready / flag unreadable), so a route answers them unchanged and a
background caller records them as the reason the order was not placed.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.halt_guard import check_not_halted

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_session: Any = None


def configure(sessionmaker: async_sessionmaker[AsyncSession] | None, session: Any) -> None:
    """Called once by the engine's lifespan. Until then the door has nothing
    to read and lets orders through, which only happens in tests that build
    an executor without the engine around it."""
    global _sessionmaker, _session
    _sessionmaker = sessionmaker
    _session = session


async def may_place() -> None:
    """Raise when no order may leave now. Cheap: one indexed read."""
    if _sessionmaker is not None:
        await check_not_halted(_sessionmaker)
    state = getattr(_session, "state", None)
    ready = getattr(state, "ready", None)
    if ready is not None and not ready.is_set():
        raise HTTPException(
            status_code=503,
            detail={"code": "reconciliation_in_progress", "reason": getattr(state, "ready_reason", None)},
        )


async def place_at_broker(ib: Any, contract: Any, order: Any) -> Any:
    """Check the door, then hand the order to IB. Nothing yields between the
    check and the send."""
    await may_place()
    return ib.placeOrder(contract, order)
