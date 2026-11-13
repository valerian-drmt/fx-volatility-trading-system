"""The one place an order is recorded as ENDED without a fill (#489).

An order ends unfilled in three ways: IB rejects it, it is cancelled, or it
expires. Four components used to write those states, each in its own way, and
each updated a different part of what must follow:

* the status handler wrote the order and nothing else: the leg a dead close
  reserved stayed reserved, and the trade stayed ``submitted`` for ever (prod
  had eleven such trades, the oldest four months old);
* the reaper released the reservation and finalised the trade;
* the api cancel finalised the trade and kept the reservation;
* the manual ticket did a third mix.

``end_order`` does all of it, in the caller's transaction:

1. the order's state, timestamps and reason;
2. the reservation of the leg it was closing;
3. the state of its trade, when every order that defines it has ended.

Filled orders do not come through here: a fill is booked by the fill handler,
which owns that edge.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.execution.reaper_policy import TERMINAL_STATES, plan_structure_terminal_state
from persistence.models import StructureOrder, TradeEvent, TradeStructure
from persistence.reservation import recompute_reservation

logger = logging.getLogger(__name__)

ENDED_STATES = ("cancelled", "rejected", "expired")
# A trade whose state can still be decided by its orders ending. A trade that
# filled, failed or closed is not rewritten because an order added to it later
# (a hedge, an unwind) ended.
_UNDECIDED = ("submitted", "partial_fill")


async def finalise_structure_if_done(
    db: AsyncSession, structure_id: int, *, now: datetime | None = None,
) -> str | None:
    """Give an undecided trade its final state once the orders that define it
    have all ended. Those are its entry orders, or its closing orders for a
    ticket of closes; a hedge or an unwind is not what makes the trade.
    Returns the new state, or None when nothing changed. Caller commits."""
    struct = await db.get(TradeStructure, structure_id)
    if struct is None or struct.state not in _UNDECIDED:
        return None
    rows = (await db.execute(
        select(StructureOrder.order_role, StructureOrder.state)
        .where(StructureOrder.structure_id == structure_id)
    )).all()
    roles = {r for r, _ in rows}
    primary = "entry" if "entry" in roles else ("closing" if "closing" in roles else None)
    if primary is None:
        return None
    new_state = plan_structure_terminal_state([s for r, s in rows if r == primary])
    if new_state is None or new_state == struct.state:
        return None
    now = now or datetime.now(UTC)
    struct.state = new_state
    struct.state_updated_at = now
    db.add(TradeEvent(
        structure_id=structure_id, order_id=None,
        event_type="structure_terminalised", severity="info",
        description=f"structure {structure_id} -> {new_state} (all {primary} orders ended)",
        payload={"structure_id": structure_id, "new_state": new_state},
    ))
    return new_state


async def end_order(
    db: AsyncSession, order: StructureOrder, state: str, *,
    reason: str | None = None, now: datetime | None = None,
) -> bool:
    """Record that ``order`` ended as ``state`` and do everything that follows
    from it. False when the order had already ended or filled: nothing is
    rewritten. Caller commits."""
    if state not in ENDED_STATES:
        raise ValueError(f"end_order writes {ENDED_STATES}, not {state!r}")
    if order.state in TERMINAL_STATES:
        return False
    now = now or datetime.now(UTC)
    order.state = state
    order.state_updated_at = now
    if state == "rejected":
        order.rejected_at = now
    if reason:
        order.rejection_text = reason[:300]
    await db.flush()
    if order.order_role == "closing" and order.closes_order_id is not None:
        await recompute_reservation(db, entry_order_id=int(order.closes_order_id))
    await finalise_structure_if_done(db, int(order.structure_id), now=now)
    return True
