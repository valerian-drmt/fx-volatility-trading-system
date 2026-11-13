"""Bring a stuck order's row in line with what happened, on EVIDENCE only (#484).

An order is "stuck" when its row is still ``submitted`` or
``partially_filled`` although something may have happened at the broker that
the engine did not record (an event before the handlers were attached, a
restart). Two sources can settle it, and nothing else:

1. **Its own recorded fills.** If ``trade_fill`` already covers the order,
   the state simply never advanced: it is set from the book.
2. **The executions IB reports for that order.** The day's executions are
   asked from IB and each one carrying the order's ``orderRef`` is booked
   through the idempotent fill path, exactly as a live fill.

What this module no longer does: conclude that an order filled because the
broker *holds some of that contract*. That rule marked a partly filled order
still working at IB as filled at its full quantity, and gave a new unfilled
order the fills of an older trade on the same contract; the book then held a
quantity the broker did not. A position is not evidence of an execution.

An order neither source can settle is left exactly as it is. The stuck-order
watcher raises it to the operator after ten minutes; the reaper decides on
its liveness. Guessing is not this module's job, nor anyone's.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.contracts import parse_local_symbol
from core.execution.fills import state_from_recorded_fills
from engines.execution.fills_handler import _lock_for, maybe_complete_structure, persist_fill, settle_book
from persistence.models import OpenPosition, StructureFill, StructureOrder, TradeEvent, TradeStructure
from persistence.projection import rebuild_leg

logger = logging.getLogger(__name__)

# Orders in these states are "stuck" (never reached a terminal state).
_STUCK_STATES = ("submitted", "acknowledged", "partially_filled")
# CME EUR FOP strikes sit on a 0.005 grid ; allow a touch more for float noise.
_STRIKE_TOL = 0.006


def _leg_matches_position(order: StructureOrder, pos: OpenPosition) -> bool:
    """True if the live IB ``pos`` is this order leg (same side + contract).

    Kept for diagnostics and tests. It is NOT used to settle an order any
    more: holding a contract does not prove that this order executed."""
    if (order.side or "").upper() != (pos.side or "").upper():
        return False
    spec = parse_local_symbol(pos.structure)
    ct = (order.contract_type or "").lower()
    if ct == "future":
        return spec is not None and spec.instrument_type == "FUTURE"
    if spec is None or spec.instrument_type != "OPTION":
        return False
    if (spec.option_type or "").lower() != ct:            # call / put
        return False
    if order.contract_strike is None or spec.strike is None:
        return True                                        # can't compare → trust side+type
    return abs(float(order.contract_strike) - float(spec.strike)) <= _STRIKE_TOL


async def _settle_from_recorded_fills(
    sm: async_sessionmaker[AsyncSession], order_id: int, now: datetime,
) -> tuple[str | None, int | None]:
    """Source 1, one order, under its lock: state from its own ``trade_fill``
    rows. Returns ``(new_state, structure_id)``; ``(None, None)`` when the
    row is not stuck any more or its fills say nothing new."""
    async with _lock_for(order_id), sm() as db:
        o = await db.get(StructureOrder, order_id)
        if o is None or o.state not in _STUCK_STATES:
            return None, None       # a handler moved it since we listed it
        recorded = int((await db.execute(
            select(func.coalesce(func.sum(StructureFill.qty_filled), 0))
            .where(StructureFill.order_id == order_id)
        )).scalar_one() or 0)
        new_state = state_from_recorded_fills(recorded, int(o.qty), o.state)
        if new_state is None:
            return None, None
        o.state = new_state
        o.state_updated_at = now
        o.qty_filled = min(recorded, int(o.qty))
        await rebuild_leg(db, order_id=order_id)  # keep the projection in step (I3)
        if new_state == "filled":
            o.fully_filled_at = now
        sid = int(o.structure_id)
        await db.commit()
        return new_state, sid


async def _executions_by_order(executor: Any) -> dict[int, list[Any]] | None:
    """Source 2: the executions IB reports, grouped by the ``trade_order`` id
    in their ``orderRef``. None when IB cannot be asked (not connected, not
    reporting): the caller then leaves every order alone."""
    if executor is None:
        return None
    try:
        if not executor.is_connected() or not executor.account_is_reporting():
            return None
        fills = await executor.replay_executions()
    except Exception:
        logger.warning("reconcile_executions_unavailable", exc_info=True)
        return None
    from engines.execution.reaper import parse_order_ref  # the reaper imports this module

    out: dict[int, list[Any]] = {}
    for f in fills:
        parsed = parse_order_ref(getattr(getattr(f, "execution", None), "orderRef", None))
        if parsed is not None:
            out.setdefault(int(parsed[1]), []).append(f)
    return out


async def reconcile_stuck_orders(
    sm: async_sessionmaker[AsyncSession], executor: Any = None,
) -> dict[str, Any]:
    """One pass. Returns a small summary for logging / the endpoint."""
    now = datetime.now(UTC)
    async with sm() as db:
        stuck_ids = [int(i) for i in (await db.execute(
            select(StructureOrder.id).where(StructureOrder.state.in_(_STUCK_STATES))
        )).scalars().all()]
    if not stuck_ids:
        stuck_ids = []

    filled_ids: list[int] = []
    affected: set[int] = set()
    executions: dict[int, list[Any]] | None = None
    booked_from_ib = 0

    # ── Source 1: the book. Never invents a fill: only orders with recorded
    # trade_fill rows move.
    for oid in stuck_ids:
        new_state, sid = await _settle_from_recorded_fills(sm, oid, now)
        if new_state == "filled" and sid is not None:
            filled_ids.append(oid)
            affected.add(sid)

    # ── Source 2: IB's own executions for these orders. persist_fill is the
    # live fill path: idempotent on the execution id, under the order's lock,
    # and it completes and settles the trade itself.
    if stuck_ids:
        executions = await _executions_by_order(executor)
    if executions:
        still_stuck = set(stuck_ids) - set(filled_ids)
        for oid in sorted(still_stuck & set(executions)):
            for fill in executions[oid]:
                try:
                    await persist_fill(fill, oid, sm)
                    booked_from_ib += 1
                except Exception:
                    logger.exception("reconcile_persist_fill_failed order_id=%s", oid)

    if affected:
        async with sm() as db:
            for sid in sorted(affected):
                db.add(TradeEvent(
                    structure_id=sid, event_type="order_reconciled_from_book", severity="info",
                    description="stuck leg(s) set to filled from their own recorded fills",
                    payload={"filled_order_ids": filled_ids},
                ))
            await db.commit()
    # A trade whose entry legs have ALL filled and that is still "submitted":
    # the engine stopped between the commit of the last fill and the step that
    # completes the trade. Nothing retried that step: the fill was already
    # there on restart, so the handler returned early, and the trade stayed
    # without a booked position (#490).
    async with sm() as db:
        filled_struct = select(StructureOrder.structure_id).where(
            StructureOrder.order_role == "entry", StructureOrder.state == "filled",
        )
        unfilled_struct = select(StructureOrder.structure_id).where(
            StructureOrder.order_role == "entry", StructureOrder.state != "filled",
        )
        incomplete = [int(i) for i in (await db.execute(
            select(TradeStructure.id)
            .where(TradeStructure.state == "submitted")
            .where(TradeStructure.id.in_(filled_struct))
            .where(TradeStructure.id.notin_(unfilled_struct))
        )).scalars().all()]
    for sid in sorted(affected | set(incomplete)):
        try:
            await maybe_complete_structure(sm, sid)
        except Exception:
            logger.exception("reconcile_complete_failed structure_id=%s", sid)
    for oid in filled_ids:
        await settle_book(sm, oid)

    if filled_ids or booked_from_ib:
        logger.info(
            "reconcile_stuck_orders filled_from_book=%s executions_booked_from_ib=%s structures=%s",
            filled_ids, booked_from_ib, sorted(affected),
        )
    return {
        "stuck": len(stuck_ids), "filled": len(filled_ids), "from_ib": booked_from_ib,
        "structures": sorted(affected), "asked_ib": executions is not None, "completed": incomplete,
    }


async def reconcile_loop(
    sm: async_sessionmaker[AsyncSession], executor: Any = None, *, interval_s: float = 60.0,
) -> None:
    """Run forever ; reconcile every ``interval_s``. Cancellable via task.cancel()."""
    while True:
        try:
            await reconcile_stuck_orders(sm, executor)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reconcile_loop_error")
        await asyncio.sleep(interval_s)
