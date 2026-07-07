"""DB side of the reservation ledger — materialise ``leg_position.reserved_qty``.

``reserved_qty`` = Σ (qty − qty_filled) over the NON-TERMINAL closing orders that
target a leg (``trade_order.closes_order_id``). It is *recomputed* (not
incrementally mutated) at every closing-order lifecycle event, so it is a pure
function of the closing orders — race-free and restart-safe (the D4 fix). The
projection (``persistence.projection``) owns ``open_qty``; this owns
``reserved_qty``; the two never write each other's column.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.execution.reservation import try_reserve
from persistence.models import LegPosition, StructureOrder

# An in-flight close still holding a reservation (everything not terminal).
_NON_TERMINAL = ("pending", "submitted", "acknowledged", "partially_filled")


async def _leg_row(db: AsyncSession, order_id: int) -> LegPosition:
    lp = (await db.execute(
        select(LegPosition).where(LegPosition.order_id == order_id)
    )).scalar_one_or_none()
    if lp is None:
        lp = LegPosition(order_id=order_id)
        db.add(lp)
    return lp


async def recompute_reservation(db: AsyncSession, *, entry_order_id: int) -> Decimal:
    """Materialise reserved_qty on an entry leg from its in-flight closing orders.

    Idempotent; caller commits. Called on close-order create / fill / terminalise —
    releasing the filled/terminal portion falls out of the recompute for free.
    """
    rows = (await db.execute(
        select(StructureOrder.qty, StructureOrder.qty_filled)
        .where(StructureOrder.closes_order_id == entry_order_id)
        .where(StructureOrder.order_role == "closing")
        .where(StructureOrder.state.in_(_NON_TERMINAL))
    )).all()
    reserved = sum(max(int(q) - int(qf or 0), 0) for q, qf in rows)
    lp = await _leg_row(db, entry_order_id)
    lp.reserved_qty = Decimal(reserved)
    return lp.reserved_qty


async def try_reserve_on_leg(
    db: AsyncSession, *, entry_order_id: int, requested: float
) -> Decimal:
    """O(1) admission guard: reserve ``requested`` on a leg or raise
    ``OverReserveError``. The building block for a race-free close admission
    (the stateless 409 re-sum's replacement)."""
    lp = await _leg_row(db, entry_order_id)
    new_reserved = try_reserve(
        open_qty=float(lp.open_qty or 0),
        reserved_qty=float(lp.reserved_qty or 0),
        requested=float(requested),
    )
    lp.reserved_qty = Decimal(str(new_reserved))
    return lp.reserved_qty
