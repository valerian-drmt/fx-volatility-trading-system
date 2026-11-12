"""A trade whose legs are all flat is closed (#462, #464).

The book decides, never the broker mirror: a ``trade_structure`` becomes
``closed`` when it has traded, has no order still working, and every entry
leg reads 0 in ``leg_position``. Its ``booked_position`` follows in the same
transaction, with the P&L realised on the legs.

Before this, no code wrote ``trade_structure.state = 'closed'``, and a
``booked_position`` was closed by comparing a rebuilt contract name with what
IB held (31 of 33 closed that way on prod, two of them still held).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import (
    BookedPosition,
    LegPosition,
    StructureOrder,
    TradeEvent,
    TradeStructure,
)

logger = logging.getLogger(__name__)

_WORKING = ("pending", "submitted", "acknowledged", "partially_filled")
# States from which a trade can be closed: it has traded.
_CLOSABLE = ("partial_fill", "fully_filled", "partial_fail")
REASON_TRADE = "closed_by_trade"
REASON_EXPIRY = "expired"


async def close_structure_if_flat(
    db: AsyncSession, *, structure_id: int, reason: str = REASON_TRADE, now: datetime | None = None,
) -> bool:
    """Close the trade when its book is flat. Idempotent; caller commits."""
    struct = await db.get(TradeStructure, structure_id)
    if struct is None or struct.state not in _CLOSABLE:
        return False
    orders = (await db.execute(
        select(StructureOrder).where(StructureOrder.structure_id == structure_id)
    )).scalars().all()
    entries = [o for o in orders if o.order_role == "entry"]
    if not entries or not any(int(o.qty_filled or 0) > 0 for o in entries):
        return False   # a ticket of closing orders, or a trade that never filled
    if any(o.state in _WORKING for o in orders):
        return False
    # Closing orders that name one of these legs and are still working keep
    # the trade open until they settle.
    entry_ids = [int(o.id) for o in entries]
    working_closes = (await db.execute(
        select(func.count(StructureOrder.id))
        .where(StructureOrder.closes_order_id.in_(entry_ids))
        .where(StructureOrder.state.in_(_WORKING))
    )).scalar_one()
    if working_closes:
        return False
    legs = (await db.execute(
        select(LegPosition).where(LegPosition.order_id.in_([int(o.id) for o in orders]))
    )).scalars().all()
    if any(float(lp.open_qty or 0) != 0.0 for lp in legs):
        return False
    # Flat needs EVIDENCE. A filled entry leg with no ``leg_position`` row has
    # no recorded execution at all (its fills never reached us): the book knows
    # nothing about it, which is not the same as holding nothing. Prod,
    # 2026-11-12: trade 87 (order 108, filled 10, zero fills) was closed by
    # this function while the broker still held the put.
    with_row = {int(lp.order_id) for lp in legs}
    if any(int(o.qty_filled or 0) > 0 and int(o.id) not in with_row for o in entries):
        return False

    now = now or datetime.now(UTC)
    struct.state = "closed"
    struct.state_updated_at = now
    struct.closed_at = now
    struct.close_reason = reason[:80]

    realized = sum(float(lp.realized_pnl_usd or 0) for lp in legs)
    close_commission = float((await db.execute(
        select(func.coalesce(func.sum(StructureOrder.total_commission_usd), 0.0))
        .where(StructureOrder.closes_order_id.in_(entry_ids))
    )).scalar_one() or 0.0)
    pos = (await db.execute(
        select(BookedPosition).where(BookedPosition.structure_id == structure_id)
    )).scalar_one_or_none()
    if pos is not None and pos.state not in ("closed", "expired"):
        pos.state = "expired" if reason == REASON_EXPIRY else "closed"
        pos.state_updated_at = now
        pos.closed_at = now
        pos.close_reason = reason[:80]
        pos.exit_total_cost_usd = round(close_commission, 2)
        pos.gross_pnl_usd = round(realized, 2)
        pos.net_pnl_usd = round(realized - float(pos.entry_total_cost_usd or 0.0) - close_commission, 2)
    db.add(TradeEvent(
        structure_id=structure_id, position_id=pos.id if pos is not None else None,
        event_type="position_closed", severity="info",
        description=f"trade {structure_id} closed: {reason}"[:300],
        payload={"reason": reason, "realized_pnl_usd": round(realized, 2),
                 "close_commission_usd": round(close_commission, 2)},
    ))
    logger.info("trade_closed_flat structure_id=%s reason=%s realized=%.2f", structure_id, reason, realized)
    return True


async def close_after_leg_change(
    db: AsyncSession, *, order_id: int, reason: str = REASON_TRADE,
) -> list[int]:
    """After a fill or an event on ``order_id``: close its trade and the trade
    of the leg it closes, when flat. Returns the ids closed."""
    order = await db.get(StructureOrder, order_id)
    if order is None:
        return []
    structure_ids = [int(order.structure_id)]
    if order.closes_order_id is not None:
        target = await db.get(StructureOrder, int(order.closes_order_id))
        if target is not None and int(target.structure_id) not in structure_ids:
            structure_ids.append(int(target.structure_id))
    closed: list[int] = []
    for sid in structure_ids:
        if await close_structure_if_flat(db, structure_id=sid, reason=reason):
            closed.append(sid)
    return closed
