"""Leg-position projection — the book, folded forward from the fill log.

Invariant I3 (OMS_ARCHITECTURE_CIBLE.md §7.1) : the position of a leg is a
pure fold over ITS OWN executions, linked by FK at write time :

    entry ``trade_order``            → its ``trade_fill`` rows
    closing ``trade_order`` rows     → matched via ``closes_order_id``
                                       (stamped at close creation, never guessed)

The fold itself is ``core.ledger`` (average-cost, realised P&L, commissions).
Nothing here reads the IB mirror (``open_position``) — destroy every
``leg_position`` row, call :func:`rebuild_all`, and the book reconstructs
identically from ``trade_fill`` alone (scenario T8).

Writers : ``engines.execution.position_projector`` (live fill cascade +
startup) and the API mock-submit path. Both call :func:`rebuild_leg` — the
row content is a deterministic function of the fill log, so any invocation
converges to the same state ; ``reserved_qty`` (the close-reservation ledger,
spec §8) is owned by the close path and preserved untouched by rebuilds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.ledger import LedgerFill, fold_fills
from persistence.models import LegPosition, StructureFill, StructureOrder
from shared.contracts import multiplier_for

_LEG_KEY = "leg"  # single-contract fold : the key is irrelevant per leg


@dataclass(frozen=True)
class LegProjection:
    """Folded book state of one leg (pure output, no ORM)."""

    order_id: int
    open_qty: float          # signed : + long, − short
    avg_price: float | None  # avg premium of the OPEN qty (None when flat)
    realized_pnl_usd: float
    n_fills: int


async def _leg_fills(db: AsyncSession, order_id: int) -> list[tuple[StructureFill, StructureOrder]]:
    """All fills of the leg — the entry order's own plus those of closing
    orders pointing at it — in execution order."""
    closing_ids = (await db.execute(
        select(StructureOrder.id).where(StructureOrder.closes_order_id == order_id)
    )).scalars().all()
    order_ids = [order_id, *closing_ids]
    rows = (await db.execute(
        select(StructureFill, StructureOrder)
        .join(StructureOrder, StructureFill.order_id == StructureOrder.id)
        .where(StructureFill.order_id.in_(order_ids))
        .order_by(StructureFill.timestamp, StructureFill.id)
    )).all()
    return list(rows)


async def project_leg(db: AsyncSession, *, order_id: int) -> LegProjection:
    """Fold one leg's fills into its book position. Read-only."""
    rows = await _leg_fills(db, order_id)
    if not rows:
        return LegProjection(order_id=order_id, open_qty=0.0, avg_price=None,
                             realized_pnl_usd=0.0, n_fills=0)
    fills = [
        LedgerFill(
            contract=_LEG_KEY,
            side=f.side,
            qty=float(f.qty_filled),
            price=float(f.fill_price),
            commission=float(f.commission_usd or 0.0),
            multiplier=multiplier_for(o.contract_symbol),
        )
        for f, o in rows
    ]
    led = fold_fills(fills)[_LEG_KEY]
    return LegProjection(
        order_id=order_id,
        open_qty=led.net_qty,
        avg_price=led.avg_cost if led.net_qty != 0 else None,
        realized_pnl_usd=led.realized_pnl,
        n_fills=len(fills),
    )


async def rebuild_leg(db: AsyncSession, *, order_id: int) -> LegPosition | None:
    """Upsert the ``leg_position`` row for one entry order from its fills.

    ``reserved_qty`` is preserved (owned by the close path). Returns the row,
    or None when the leg has no fills and no existing row (nothing to
    materialise). The caller commits.
    """
    proj = await project_leg(db, order_id=order_id)
    row = (await db.execute(
        select(LegPosition).where(LegPosition.order_id == order_id).limit(1)
    )).scalar_one_or_none()
    if row is None:
        if proj.n_fills == 0:
            return None
        row = LegPosition(order_id=order_id, reserved_qty=0)
        db.add(row)
    row.open_qty = proj.open_qty
    row.avg_price = proj.avg_price
    row.realized_pnl_usd = proj.realized_pnl_usd
    row.rebuilt_at = datetime.now(UTC)
    return row


async def rebuild_all(db: AsyncSession) -> int:
    """Rebuild every leg that has fills or an existing row. Returns the count.

    Legs are entry orders ; closing orders never get their own row — their
    fills fold into the leg they point at.
    """
    entry_ids = set((await db.execute(
        select(StructureOrder.id).where(StructureOrder.order_role == "entry")
    )).scalars().all())
    with_rows = set((await db.execute(select(LegPosition.order_id))).scalars().all())
    with_fills = set((await db.execute(
        select(StructureOrder.id)
        .join(StructureFill, StructureFill.order_id == StructureOrder.id)
        .where(StructureOrder.order_role == "entry")
    )).scalars().all())
    # Closing fills can exist while the entry leg itself never filled.
    closed_into = set((await db.execute(
        select(StructureOrder.closes_order_id)
        .join(StructureFill, StructureFill.order_id == StructureOrder.id)
        .where(StructureOrder.closes_order_id.is_not(None))
    )).scalars().all())

    targets = (((with_fills | closed_into) & entry_ids) | with_rows) - {None}
    n = 0
    for order_id in sorted(targets):
        if await rebuild_leg(db, order_id=order_id) is not None:
            n += 1
    return n
