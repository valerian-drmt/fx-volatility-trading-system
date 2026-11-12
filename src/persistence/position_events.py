"""Writers of ``position_event`` (#463, #465): the only way, besides a fill,
to change what a leg holds. Each writer is idempotent on
``(order_id, kind, dedup_key)`` and rebuilds the leg it touched.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import LegPosition, PositionEvent, StructureFill, StructureOrder
from persistence.projection import rebuild_leg


async def _exists(db: AsyncSession, order_id: int, kind: str, dedup_key: str) -> bool:
    return (await db.execute(
        select(PositionEvent.id)
        .where(PositionEvent.order_id == order_id)
        .where(PositionEvent.kind == kind)
        .where(PositionEvent.dedup_key == dedup_key)
        .limit(1)
    )).scalar_one_or_none() is not None


async def record_expiry(
    db: AsyncSession, *, order: StructureOrder, expiry: date, price: float = 0.0,
    source: str = "lifecycle", note: str | None = None, now: datetime | None = None,
) -> PositionEvent | None:
    """Bring a leg to zero because its contract expired. No-op when the leg
    is already flat or the expiry is already recorded. Caller commits."""
    lp = (await db.execute(
        select(LegPosition).where(LegPosition.order_id == order.id)
    )).scalar_one_or_none()
    open_qty = float(lp.open_qty or 0) if lp is not None else 0.0
    key = expiry.isoformat()
    if open_qty == 0.0 or await _exists(db, int(order.id), "expiry", key):
        return None
    ev = PositionEvent(
        order_id=int(order.id), kind="expiry",
        side="SELL" if open_qty > 0 else "BUY", qty=Decimal(str(abs(open_qty))),
        price=Decimal(str(price)),
        occurred_at=now or datetime.now(UTC), dedup_key=key, source=source[:40],
        note=(note or "")[:200] or None,
    )
    db.add(ev)
    await db.flush()
    await rebuild_leg(db, order_id=int(order.id))
    return ev


async def record_repair(
    db: AsyncSession, *, order: StructureOrder, price: float | None,
    source: str = "order_reconciler", now: datetime | None = None,
) -> PositionEvent | None:
    """Give a filled order with missing executions its book quantity: one
    ``repair`` event for the part its recorded fills do not cover."""
    recorded = int((await db.execute(
        select(func.coalesce(func.sum(StructureFill.qty_filled), 0))
        .where(StructureFill.order_id == order.id)
    )).scalar_one() or 0)
    missing = int(order.qty_filled or 0) - recorded
    if missing <= 0 or await _exists(db, int(order.id), "repair", "missing_fills"):
        return None
    ev = PositionEvent(
        order_id=int(order.id), kind="repair", side=order.side, qty=Decimal(missing),
        price=Decimal(str(price if price is not None else 0.0)),
        occurred_at=now or datetime.now(UTC), dedup_key="missing_fills", source=source[:40],
        note="order filled at the broker, executions never received; price is an estimate",
    )
    db.add(ev)
    await db.flush()
    await rebuild_leg(db, order_id=int(order.id))
    return ev
