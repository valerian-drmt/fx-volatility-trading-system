"""Close-reservation ledger — DB side of invariant I5 (spec §8).

``leg_position.reserved_qty`` materialises the quantity already committed to
in-flight closes, so ``available = |open_qty| − reserved_qty`` is O(1) state
that survives restarts — not a volatile re-sum.

Two operations :

  * :func:`try_reserve_on_leg` — ADMISSION, called when a close is created.
    A single conditional UPDATE (``WHERE |open| − reserved ≥ qty``) makes the
    check-and-reserve atomic : two racing closes cannot both pass, which is
    what kills root defect D4 (stacked over-closes).
  * :func:`recompute_reservation` — RELEASE, called after any event that
    changes a closing order's state (fill, cancel, reject, reap). Rather
    than decrementing per event (drift-prone), it re-folds the steady-state
    identity : reserved == Σ (qty − qty_filled) over NON-terminal closing
    orders targeting the leg (pure fold in ``core.execution.reservation``).

Both converge to the same fold, so the admission UPDATE and the recompute
can safely interleave. Callers commit.
"""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.execution.reservation import outstanding_reservation
from persistence.models import LegPosition, StructureOrder

#: Absorbing order states — a terminal close holds no reservation.
TERMINAL_STATES: frozenset[str] = frozenset({"filled", "rejected", "cancelled", "expired"})


async def try_reserve_on_leg(
    db: AsyncSession, *, leg_order_id: int, qty: float,
) -> bool:
    """Atomically reserve ``qty`` on the leg. False = refused (over-close).

    True is also returned when the leg has no ``leg_position`` row yet (no
    fills → nothing to over-close ; the mirror-level qty check still guards
    those). The caller commits.
    """
    row_exists = (await db.execute(
        select(LegPosition.id).where(LegPosition.order_id == leg_order_id).limit(1)
    )).scalar_one_or_none()
    if row_exists is None:
        return True
    res = await db.execute(
        update(LegPosition)
        .where(
            LegPosition.order_id == leg_order_id,
            func.abs(LegPosition.open_qty) - LegPosition.reserved_qty >= qty,
        )
        .values(reserved_qty=LegPosition.reserved_qty + qty)
    )
    return res.rowcount > 0


async def recompute_reservation(
    db: AsyncSession, *, leg_order_id: int,
) -> float | None:
    """Re-fold the leg's reservation from its non-terminal closing orders.

    Returns the new reserved value, or None when the leg has no row. The
    caller commits.
    """
    row = (await db.execute(
        select(LegPosition).where(LegPosition.order_id == leg_order_id).limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None
    closes = (await db.execute(
        select(StructureOrder.qty, StructureOrder.qty_filled, StructureOrder.state)
        .where(StructureOrder.closes_order_id == leg_order_id)
    )).all()
    reserved = outstanding_reservation(
        [(float(q or 0), float(qf or 0), s) for q, qf, s in closes],
        terminal_states=TERMINAL_STATES,
    )
    row.reserved_qty = reserved
    return reserved
