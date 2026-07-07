"""Position reconciler — materialises book↔broker breaks (invariant I4).

The homeostasis loop of OMS_ARCHITECTURE_CIBLE.md §9 : setpoint ``break = 0``.
Each pass diffs the BOOK (Σ ``leg_position.open_qty`` per contract — the
forward projection, our truth) against the MIRROR (Σ ``open_position`` signed
net per contract — what IB last reported) and persists every divergence as a
``reconciliation_break`` row :

  * at most one OPEN row per contract — updated (`last_seen_at`, quantities)
    while the gap persists ;
  * when the sides agree again, the open row gets ``resolved_at`` stamped ;
  * a later re-break opens a NEW row — the audit history is append-preserved.

The classification is `core.execution.reconciliation` — the same pure diff
the `/positions/reconciliation` endpoint uses.

The pass itself only reads the DB ; the LOOP is guarded by
``executor.account_is_reporting()`` so a dead feed (stale/empty mirror) never
manufactures break rows (spec §7.2 / T7).

Single writer of ``reconciliation_break``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.reconciliation import compute_breaks
from persistence.models import (
    LegPosition,
    OpenPosition,
    ReconciliationBreak,
    StructureOrder,
)

logger = logging.getLogger(__name__)


async def _book_by_contract(db: AsyncSession) -> dict[str, float]:
    """Σ leg_position.open_qty per localSymbol — the forward book."""
    rows = (await db.execute(
        select(StructureOrder.ib_local_symbol, LegPosition.open_qty)
        .join(StructureOrder, LegPosition.order_id == StructureOrder.id)
        .where(StructureOrder.ib_local_symbol.is_not(None))
    )).all()
    out: dict[str, float] = {}
    for sym, qty in rows:
        out[sym] = out.get(sym, 0.0) + float(qty or 0)
    return out


async def _broker_by_contract(db: AsyncSession) -> dict[str, float]:
    """Σ open_position signed net per localSymbol — the IB mirror."""
    rows = (await db.execute(
        select(OpenPosition.structure, OpenPosition.side, OpenPosition.quantity)
    )).all()
    out: dict[str, float] = {}
    for sym, side, qty in rows:
        signed = float(qty or 0) * (1 if (side or "").upper() == "BUY" else -1)
        out[sym] = out.get(sym, 0.0) + signed
    return out


async def reconcile_positions(
    *, sessionmaker_factory: async_sessionmaker[AsyncSession],
) -> int:
    """One reconciliation pass. Returns the number of OPEN breaks after it."""
    now = datetime.now(UTC)
    async with sessionmaker_factory() as db:
        book = await _book_by_contract(db)
        broker = await _broker_by_contract(db)
        found = {b.contract: b for b in compute_breaks(book, broker)}

        open_rows = {
            r.local_symbol: r
            for r in (await db.execute(
                select(ReconciliationBreak)
                .where(ReconciliationBreak.resolved_at.is_(None))
            )).scalars().all()
        }

        for contract, brk in found.items():
            row = open_rows.get(contract)
            if row is None:
                db.add(ReconciliationBreak(
                    local_symbol=contract[:20],
                    book_qty=brk.book_qty, broker_qty=brk.broker_qty,
                    diff=brk.diff, break_type=brk.break_type,
                    detected_at=now, last_seen_at=now,
                ))
                logger.warning(
                    "reconciliation_break_opened contract=%s type=%s book=%.4f broker=%.4f",
                    contract, brk.break_type, brk.book_qty, brk.broker_qty,
                )
            else:
                row.book_qty = brk.book_qty
                row.broker_qty = brk.broker_qty
                row.diff = brk.diff
                row.break_type = brk.break_type
                row.last_seen_at = now

        for contract, row in open_rows.items():
            if contract not in found:
                row.resolved_at = now
                row.last_seen_at = now
                logger.info("reconciliation_break_resolved contract=%s", contract)

        await db.commit()
    return len(found)


async def reconcile_loop(
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,
    *,
    interval_s: float = 60.0,
) -> None:
    """Run forever ; one guarded pass every ``interval_s``. Cancellable.

    Guard : never diff against the mirror when the feed is dead — an empty
    snapshot would open ``missing_at_ib`` breaks for every book holding.
    """
    logger.info("reconcile_loop_started interval=%.1fs", interval_s)
    while True:
        try:
            if executor.account_is_reporting():
                await reconcile_positions(sessionmaker_factory=sessionmaker_factory)
            else:
                logger.debug("reconcile_skipped account_not_reporting")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reconcile_cycle_crashed")
        await asyncio.sleep(interval_s)
