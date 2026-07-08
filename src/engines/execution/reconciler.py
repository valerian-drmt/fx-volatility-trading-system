"""Position reconciliation loop — materialise book vs broker breaks (I4).

Distinct from ``order_reconciler`` (which backfills stuck *orders* to filled):
this is the book↔broker feedback loop whose setpoint is ``break = 0``. Every
``RECONCILE_POSITIONS_INTERVAL_S`` it diffs the forward book (Σ
``leg_position.open_qty`` per contract) against the netted IB mirror
(``open_position``) and upserts ``reconciliation_break`` rows — one open row per
contract, resolved when the gap closes. Guarded by ``account_is_reporting`` (a
dead feed is not "IB is flat", so it never fabricates a break).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.reconciliation import compute_breaks
from persistence.models import (
    LegPosition,
    OpenPosition,
    ReconciliationBreak,
    StructureOrder,
)

logger = logging.getLogger(__name__)

RECONCILE_POSITIONS_INTERVAL_S = float(
    os.getenv("RECONCILE_POSITIONS_INTERVAL_S", "60.0")
)


def _dec(x: float) -> Decimal:
    return Decimal(str(x))


async def _book_by_contract(db: AsyncSession) -> dict[str, float]:
    """Σ leg_position.open_qty per contract (the forward book), keyed on the
    leg's IB localSymbol."""
    rows = (await db.execute(
        select(StructureOrder.ib_local_symbol, func.sum(LegPosition.open_qty))
        .join(LegPosition, LegPosition.order_id == StructureOrder.id)
        .where(StructureOrder.ib_local_symbol.is_not(None))
        .group_by(StructureOrder.ib_local_symbol)
    )).all()
    return {sym: float(q or 0) for sym, q in rows if sym}


async def _broker_by_contract(db: AsyncSession) -> dict[str, float]:
    """Signed net per contract from the IB mirror (the checksum, I7)."""
    rows = (await db.execute(select(OpenPosition))).scalars().all()
    out: dict[str, float] = {}
    for p in rows:
        if not p.structure:
            continue
        q = float(p.quantity or 0)
        signed = q if (p.side or "").upper() == "BUY" else -q
        out[p.structure] = out.get(p.structure, 0.0) + signed
    return out


async def reconcile_positions(
    sm: async_sessionmaker[AsyncSession],
    executor: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One reconciliation pass. Upserts open breaks, resolves closed ones."""
    if not executor.account_is_reporting():
        return {"open_breaks": 0, "skipped": "account_not_reporting"}

    now = now or datetime.now(UTC)
    async with sm() as db:
        book = await _book_by_contract(db)
        broker = await _broker_by_contract(db)
        breaks = {b.contract: b for b in compute_breaks(book, broker)}

        open_rows = (await db.execute(
            select(ReconciliationBreak).where(ReconciliationBreak.resolved_at.is_(None))
        )).scalars().all()
        open_by_contract = {r.local_symbol: r for r in open_rows}

        for contract, br in breaks.items():
            existing = open_by_contract.get(contract)
            if existing is None:
                db.add(ReconciliationBreak(
                    local_symbol=contract,
                    book_qty=_dec(br.book_qty), broker_qty=_dec(br.broker_qty),
                    diff=_dec(br.diff), break_type=br.break_type,
                    detected_at=now, last_seen_at=now,
                ))
            else:
                existing.book_qty = _dec(br.book_qty)
                existing.broker_qty = _dec(br.broker_qty)
                existing.diff = _dec(br.diff)
                existing.break_type = br.break_type
                existing.last_seen_at = now

        # Resolve any open break whose contract is back in sync.
        for contract, row in open_by_contract.items():
            if contract not in breaks:
                row.resolved_at = now

        await db.commit()

    return {"open_breaks": len(breaks)}


async def reconcile_positions_loop(
    sm: async_sessionmaker[AsyncSession],
    executor: Any,
    *,
    interval_s: float = RECONCILE_POSITIONS_INTERVAL_S,
) -> None:
    """Run forever; reconcile every ``interval_s``. Cancellable via task.cancel()."""
    while True:
        try:
            await reconcile_positions(sm, executor)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reconcile_positions_loop_error")
        await asyncio.sleep(interval_s)
