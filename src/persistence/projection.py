"""DB side of the forward projection — materialise ``leg_position`` from fills.

Single writer of ``leg_position`` (the ``position_projector`` role). Wraps the
pure ``core.execution.projection`` fold: read a leg's ``trade_fill`` rows, fold
them, upsert the row. ``reserved_qty`` is owned by the reservation ledger (P2)
and is never touched here — a rebuild only refreshes the fill-derived fields, so
projection and reservation stay independent single-writers of their own columns.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.execution.projection import Fill, fold_fills
from persistence.models import LegPosition, StructureFill


async def rebuild_leg(db: AsyncSession, *, order_id: int) -> LegPosition | None:
    """Rebuild one leg's ``leg_position`` from its fills. Idempotent; caller commits."""
    fills = (await db.execute(
        select(StructureFill).where(StructureFill.order_id == order_id)
    )).scalars().all()
    fold = fold_fills([
        Fill(side=f.side, qty=float(f.qty_filled), price=float(f.fill_price))
        for f in fills
    ])

    lp = (await db.execute(
        select(LegPosition).where(LegPosition.order_id == order_id)
    )).scalar_one_or_none()
    if lp is None:
        lp = LegPosition(order_id=order_id)
        db.add(lp)
    lp.open_qty = Decimal(str(fold.open_qty))
    lp.avg_price = Decimal(str(fold.avg_price)) if fold.avg_price is not None else None
    lp.rebuilt_at = datetime.now(UTC)
    return lp


async def rebuild_all(db: AsyncSession) -> int:
    """Rebuild every leg that has at least one fill. Startup seed / recovery."""
    order_ids = (await db.execute(
        select(StructureFill.order_id).distinct()
    )).scalars().all()
    for oid in order_ids:
        await rebuild_leg(db, order_id=int(oid))
    return len(order_ids)
