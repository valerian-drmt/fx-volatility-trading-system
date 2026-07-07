"""Position projector — the single live writer of ``leg_position``.

Thin engine wrapper over :mod:`persistence.projection` (where the fold and
the upsert live, importable by the API mock path too). The execution engine
invokes it from exactly two places :

  * the fill cascade (``fills_handler``) — after each persisted execution,
    the affected leg is rebuilt : the entry order's own leg, or, for a
    closing order, the leg its ``closes_order_id`` points at ;
  * startup — one full :func:`rebuild_all_legs` pass, because the projection
    is a deterministic function of the append-only fill log and needs no
    polling loop : it can only change when a fill lands.

Never reads the IB mirror (invariant I7's write side).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.models import StructureOrder
from persistence.projection import LegProjection, project_leg, rebuild_all, rebuild_leg
from persistence.reservation import recompute_reservation

__all__ = ["LegProjection", "project_leg", "rebuild_all_legs", "rebuild_for_order"]

logger = logging.getLogger(__name__)


async def rebuild_for_order(
    sessionmaker_factory: async_sessionmaker[AsyncSession], order_id: int,
) -> None:
    """Rebuild the leg affected by a fill on ``order_id`` (entry or closing)."""
    try:
        async with sessionmaker_factory() as db:
            order = await db.get(StructureOrder, order_id)
            if order is None:
                return
            leg_id = (
                order.closes_order_id
                if order.order_role == "closing" and order.closes_order_id is not None
                else order.id if order.order_role == "entry"
                else None
            )
            if leg_id is None:
                # Unattributable closing fill (or hedge) : no leg to fold into.
                # Reconciliation (I4) surfaces the resulting book↔broker gap.
                logger.warning(
                    "projection_skip_unattributed order_id=%s role=%s",
                    order_id, order.order_role,
                )
                return
            await rebuild_leg(db, order_id=leg_id)
            if order.order_role == "closing":
                # A closing fill releases its share of the reservation (I5) :
                # re-fold reserved from the leg's non-terminal closes.
                await recompute_reservation(db, leg_order_id=leg_id)
            await db.commit()
    except Exception:
        logger.exception("projection_rebuild_failed order_id=%s", order_id)


async def rebuild_all_legs(
    sessionmaker_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Full projection rebuild (startup / recovery). Returns legs rebuilt."""
    try:
        async with sessionmaker_factory() as db:
            n = await rebuild_all(db)
            await db.commit()
        logger.info("projection_rebuilt_all legs=%d", n)
        return n
    except Exception:
        logger.exception("projection_rebuild_all_failed")
        return 0
