"""The one place an order's state is written (#489).

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

The other edges are here too, so that no component assigns ``state`` itself:

* ``mark_submitted``: pending to submitted, never backwards. The api used to
  write ``submitted`` on a close after the engine answered, over a fill the
  handler had booked in between.
* ``apply_fill_state``: what the recorded fills say (partly filled, filled).
* ``revive_order``: an ended row that IB still lists as open.

Every function takes the database row lock first (``lock_order``): the api and
the engine are two processes, and the engine's in-memory lock never excluded
the api.

One rule for an order that ends without a fill (#485): it is ``cancelled``
when the desk asked for the cancel (``cancel_requested_at``), ``rejected``
when the broker gave a reason, and ``expired`` otherwise, whoever sees it
first. A DAY order the broker ends at the close of the day is therefore
``expired`` from the status handler as from the reaper.
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
EXPIRED_AT_BROKER = "ended unfilled at the broker, with no cancel from the desk"


async def lock_order(db: AsyncSession, order: StructureOrder) -> None:
    """Take the row lock on ``order`` and read it as it is NOW. Held until the
    caller's transaction ends. What the caller already changed is flushed
    first, so nothing is lost by the re-read."""
    await db.flush()
    await db.refresh(order, with_for_update=True)


def state_of_broker_cancel(order: StructureOrder) -> str:
    """The state of an order the broker reports cancelled with no reason."""
    return "cancelled" if order.cancel_requested_at is not None else "expired"


async def mark_submitted(
    db: AsyncSession, order: StructureOrder, *,
    ib_order_id: str | None = None, ib_perm_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Record that the order left for the broker. Only a ``pending`` row moves;
    a row a handler already took further (filled, ended) keeps its state. The
    broker's ids are recorded either way. True when the state moved. Caller
    commits."""
    await lock_order(db, order)
    now = now or datetime.now(UTC)
    if ib_order_id and not order.ib_order_id:
        order.ib_order_id = ib_order_id
    if ib_perm_id and not order.ib_perm_id:
        order.ib_perm_id = ib_perm_id
    if order.submitted_at is None:
        order.submitted_at = now
    if order.state != "pending":
        return False
    order.state = "submitted"
    order.state_updated_at = now
    return True


def apply_fill_state(order: StructureOrder, *, fully_filled: bool, now: datetime | None = None) -> str | None:
    """Write what the recorded fills say. A filled order stays filled; an
    order that ended stays ended when a late partial fill is booked on it (it
    used to flip back and be expired again). Returns the state written, or
    None when the row is left as it is. The caller holds the lock."""
    now = now or datetime.now(UTC)
    if fully_filled:
        if order.state == "filled":
            return None
        order.state = "filled"
        order.state_updated_at = now
        order.fully_filled_at = order.fully_filled_at or now
        # a filled order was not refused: a notice kept from before the fill
        # (10349, a price cap) is not shown as a reason
        order.rejection_text = None
        return "filled"
    if order.state in TERMINAL_STATES or order.state == "partially_filled":
        return None
    order.state = "partially_filled"
    order.state_updated_at = now
    return "partially_filled"


def revive_order(order: StructureOrder, *, now: datetime | None = None) -> None:
    """An ended row the broker still lists as OPEN goes back to submitted: the
    broker is the authority on its own orders. The caller holds the lock."""
    now = now or datetime.now(UTC)
    order.state = "submitted"
    order.state_updated_at = now
    order.rejected_at = None
    order.rejection_text = None
    order.acknowledged_at = order.acknowledged_at or now
# A trade whose state can still be decided by its orders ending. A trade that
# filled, failed or closed is not rewritten because an order added to it later
# (a hedge, an unwind) ended.
_UNDECIDED = ("submitted",)


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
    await lock_order(db, order)
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
