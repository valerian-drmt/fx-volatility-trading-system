"""Hedge on fill: the futures of a package follow the option fills (#482).

When an option leg of a trade fills, the engine brings the trade's futures
hedge to the share of the options that has filled:

* nothing is sent before a fill, so unfilled options never leave naked
  futures;
* a partial fill is hedged in proportion;
* the hedge is an order of the SAME trade (``trade_order.order_role =
  'hedge'``), so it is in the book, on the position panels and in the blotter
  like any leg, and the trade is flat only when it is closed too.

The hedge was decided with the structure: the pre-trade gate judged the
package with it, so placing it is carrying out an accepted order, not a new
risk decision. It is sent even when trading is halted, because it reduces
the risk of a position that exists.

A hedge that cannot be placed is a ``rejected`` order with the reason and a
critical event: the trade is then visibly unhedged, never silently so.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.package_hedge import hedge_target, underlying_future_month
from engines.execution.fills_handler import attach_fill_handlers
from engines.execution.live_submit import order_ref as structure_order_ref
from engines.execution.order_executor import OrderRequest
from engines.execution.placement import claim_submit, stamp_placement
from persistence.models import StructureOrder, TradeEvent, TradePreviewRow, TradeStructure

logger = logging.getLogger(__name__)

HEDGE_ROLE = "hedge"
# Hedge legs are numbered from here inside their trade, clear of the entries.
HEDGE_LEG_BASE = 100
# A hedge order that did not happen: it does not count as hedge in place.
_DEAD = ("rejected", "cancelled", "expired")

_locks: dict[int, asyncio.Lock] = {}


def _lock_for(structure_id: int) -> asyncio.Lock:
    lock = _locks.get(structure_id)
    if lock is None:
        lock = _locks[structure_id] = asyncio.Lock()
    return lock


async def _hedge_in_place(db: AsyncSession, structure_id: int) -> int:
    """Futures already hedging this trade: what live hedge orders ask for,
    plus what dead ones filled before they died."""
    rows = (await db.execute(
        select(StructureOrder.qty, StructureOrder.qty_filled, StructureOrder.state)
        .where(StructureOrder.structure_id == structure_id)
        .where(StructureOrder.order_role == HEDGE_ROLE)
    )).all()
    return sum(int(qf or 0) if state in _DEAD else int(q or 0) for q, qf, state in rows)


async def hedge_after_fill(
    sm: async_sessionmaker[AsyncSession], executor: Any, order_id: int, *, now: datetime | None = None,
) -> dict[str, Any] | None:
    """Called after every booked fill. Places the futures missing for the
    options filled so far; returns what it did, None when there is nothing
    to do for this order."""
    now = now or datetime.now(UTC)
    async with sm() as db:
        order = await db.get(StructureOrder, order_id)
        if order is None or order.order_role != "entry" or (order.contract_type or "") == "future":
            return None
        structure_id = int(order.structure_id)
    async with _lock_for(structure_id), sm() as db:
        struct = await db.get(TradeStructure, structure_id)
        if struct is None or not struct.preview_id:
            return None
        preview = (await db.execute(
            select(TradePreviewRow).where(TradePreviewRow.preview_id == struct.preview_id)
        )).scalar_one_or_none()
        plan = ((preview.structure_full_payload or {}).get("hedge") if preview is not None else None) or {}
        hedge_qty = int(plan.get("qty") or 0)
        side = str(plan.get("side") or "")
        if hedge_qty <= 0 or side not in ("BUY", "SELL"):
            return None
        ordered, filled = (await db.execute(
            select(func.coalesce(func.sum(StructureOrder.qty), 0),
                   func.coalesce(func.sum(StructureOrder.qty_filled), 0))
            .where(StructureOrder.structure_id == structure_id)
            .where(StructureOrder.order_role == "entry")
            .where(StructureOrder.contract_type != "future")
        )).one()
        target = hedge_target(hedge_qty, float(filled or 0), float(ordered or 0))
        in_place = await _hedge_in_place(db, structure_id)
        need = target - in_place
        if need <= 0:
            return {"structure_id": structure_id, "target": target, "in_place": in_place, "placed": 0}

        n_hedges = int((await db.execute(
            select(func.count(StructureOrder.id))
            .where(StructureOrder.structure_id == structure_id)
            .where(StructureOrder.order_role == HEDGE_ROLE)
        )).scalar_one() or 0)
        month = underlying_future_month(order.ib_local_symbol, now.date())
        hedge = StructureOrder(
            structure_id=structure_id, leg_idx=HEDGE_LEG_BASE + n_hedges, order_role=HEDGE_ROLE,
            contract_symbol="EUR", contract_type="future", side=side, qty=need,
            order_type="MKT", state="pending", state_updated_at=now, trace_id=order.trace_id,
        )
        db.add(hedge)
        await db.commit()
        hedge_id = int(hedge.id)

        try:
            if month is None:
                raise ValueError(f"no underlying future for {order.ib_local_symbol!r}")
            await claim_submit(sm, StructureOrder, hedge_id)
            trade = await executor.place_order_trade(
                OrderRequest(symbol="EUR", sec_type="FUT", side=side, qty=need, limit_price=None,
                             expiry=month, exchange="CME", currency="USD"),
                order_ref=structure_order_ref(structure_id, hedge_id),
            )
            attach_fill_handlers(trade=trade, order_id=hedge_id, sessionmaker_factory=sm)
            await stamp_placement(sm, StructureOrder, hedge_id, trade)
        except Exception as e:
            row = await db.get(StructureOrder, hedge_id)
            if row is not None:
                row.state = "rejected"
                row.state_updated_at = now
                row.rejected_at = now
                row.rejection_text = f"hedge not placed: {e}"[:300]
            db.add(TradeEvent(
                structure_id=structure_id, order_id=hedge_id,
                event_type="package_hedge_failed", severity="critical",
                description=f"trade {structure_id}: hedge {side} {need} future(s) not placed, the trade is unhedged for that quantity"[:300],
                payload={"side": side, "qty": need, "target": target, "in_place": in_place, "error": str(e)[:300]},
            ))
            await db.commit()
            logger.exception("package_hedge_failed structure_id=%s side=%s qty=%s", structure_id, side, need)
            return {"structure_id": structure_id, "target": target, "in_place": in_place, "placed": 0, "error": str(e)}

        db.add(TradeEvent(
            structure_id=structure_id, order_id=hedge_id,
            event_type="package_hedge_placed", severity="info",
            description=f"hedge {side} {need} x 6E {month} for {int(filled or 0)}/{int(ordered or 0)} options filled"[:300],
            payload={"side": side, "qty": need, "target": target, "in_place": in_place, "contract_month": month},
        ))
        await db.commit()
        logger.info(
            "package_hedge_placed structure_id=%s side=%s qty=%s month=%s target=%s",
            structure_id, side, need, month, target,
        )
        return {"structure_id": structure_id, "target": target, "in_place": in_place, "placed": need, "order_id": hedge_id}
