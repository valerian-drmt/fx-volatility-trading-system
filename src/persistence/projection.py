"""DB side of the forward projection — materialise ``leg_position`` from fills.

Single writer of ``leg_position`` (the ``position_projector`` role). Wraps the
pure ``core.execution.projection`` fold: read everything the book attributes
to a leg, fold it, upsert the row. ``reserved_qty`` is owned by the reservation
ledger (P2) and is never touched here — a rebuild only refreshes the
fill-derived fields, so projection and reservation stay independent
single-writers of their own columns.

What is attributed to a leg (#462, #463):

* its own ``trade_fill`` rows;
* the fills of every closing order that names it in ``closes_order_id``: a
  close REDUCES the leg it closes. The closing order's own row reads 0, it
  holds nothing;
* its ``position_event`` rows (expiry, exercise, assignment, repair).

A closing order with no ``closes_order_id`` keeps its own signed quantity, so
the per-contract sum the reconciler uses is unchanged in every case.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.contracts import order_multiplier
from core.execution.projection import BookEntry, Fill, fold_fills, net_leg
from persistence.models import LegPosition, PositionEvent, StructureFill, StructureOrder

# Roles whose fills are attributed to the leg named by ``closes_order_id``.
_ATTRIBUTED_ROLES = ("closing",)


def is_attributed_close(order: StructureOrder | None) -> bool:
    return (
        order is not None
        and order.order_role in _ATTRIBUTED_ROLES
        and order.closes_order_id is not None
    )


async def _leg_row(db: AsyncSession, order_id: int) -> LegPosition:
    lp = (await db.execute(
        select(LegPosition).where(LegPosition.order_id == order_id)
    )).scalar_one_or_none()
    if lp is None:
        lp = LegPosition(order_id=order_id)
        db.add(lp)
    return lp


async def rebuild_leg(db: AsyncSession, *, order_id: int, _follow: bool = True) -> LegPosition | None:
    """Rebuild one leg's ``leg_position``. Idempotent; caller commits.

    Rebuilding a closing order also rebuilds the leg it closes."""
    order = await db.get(StructureOrder, order_id)
    own_fills = (await db.execute(
        select(StructureFill).where(StructureFill.order_id == order_id)
        .order_by(StructureFill.timestamp, StructureFill.id)
    )).scalars().all()
    now = datetime.now(UTC)
    lp = await _leg_row(db, order_id)

    if is_attributed_close(order):
        # The close holds nothing: its quantity lives on the leg it closes.
        own = fold_fills(Fill(side=f.side, qty=float(f.qty_filled), price=float(f.fill_price)) for f in own_fills)
        lp.open_qty = Decimal("0")
        lp.avg_price = Decimal(str(own.avg_price)) if own.avg_price is not None else None
        lp.realized_pnl_usd = Decimal("0")
        lp.rebuilt_at = now
        if _follow:
            await db.flush()
            await rebuild_leg(db, order_id=int(order.closes_order_id), _follow=False)
        return lp

    timeline: list[tuple[datetime, int, int, BookEntry]] = [
        (f.timestamp, 0, int(f.id or 0),
         BookEntry(side=f.side, qty=float(f.qty_filled), price=float(f.fill_price), own=True))
        for f in own_fills
    ]
    close_fills = (await db.execute(
        select(StructureFill)
        .join(StructureOrder, StructureOrder.id == StructureFill.order_id)
        .where(StructureOrder.closes_order_id == order_id)
        .where(StructureOrder.order_role.in_(_ATTRIBUTED_ROLES))
    )).scalars().all()
    timeline += [
        (f.timestamp, 1, int(f.id or 0),
         BookEntry(side=f.side, qty=float(f.qty_filled), price=float(f.fill_price), own=False))
        for f in close_fills
    ]
    events = (await db.execute(
        select(PositionEvent).where(PositionEvent.order_id == order_id)
    )).scalars().all()
    # A repair stands in for executions the engine never received: it is the
    # leg's own execution, counted only for what the recorded fills do not
    # cover, so real fills that arrive later (reconnect replay) replace it
    # instead of doubling the leg. Every other event closes quantity.
    own_filled = sum(float(f.qty_filled) for f in own_fills)
    repair_room = max(0.0, float(order.qty) - own_filled) if order is not None else float("inf")
    for e in events:
        qty = float(e.qty)
        if e.kind == "repair":
            qty = min(qty, repair_room)
            repair_room -= qty
        timeline.append((
            e.occurred_at, 2, int(e.id or 0),
            BookEntry(side=e.side, qty=qty, price=float(e.price or 0), own=(e.kind == "repair")),
        ))
    timeline.sort(key=lambda t: (_aware(t[0]), t[1], t[2]))
    mult = order_multiplier(order.contract_type, order.contract_symbol) if order is not None else 1.0
    net = net_leg([t[3] for t in timeline], multiplier=mult)

    lp.open_qty = Decimal(str(net.open_qty))
    lp.avg_price = Decimal(str(net.avg_price)) if net.avg_price is not None else None
    lp.realized_pnl_usd = Decimal(str(round(net.realized_pnl_usd, 2)))
    lp.rebuilt_at = now
    return lp


def _aware(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.min.replace(tzinfo=UTC)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


async def rebuild_all(db: AsyncSession) -> int:
    """Rebuild every leg that has a fill or an event. Startup seed / recovery."""
    order_ids = set((await db.execute(
        select(StructureFill.order_id).distinct()
    )).scalars().all())
    order_ids |= set((await db.execute(
        select(PositionEvent.order_id).distinct()
    )).scalars().all())
    for oid in sorted(int(o) for o in order_ids):
        await rebuild_leg(db, order_id=oid)
        await db.flush()
    return len(order_ids)
