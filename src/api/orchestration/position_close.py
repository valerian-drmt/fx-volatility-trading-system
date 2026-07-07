"""Initiate the close of an open position (cf. STEP5 §9.3).

Creates a *new* ``trade_structures`` row with all closing legs (one per
filled entry leg, opposite side, qty = entry.qty_filled) and POSTs to
execution-engine ``/internal/structure/submit``. The fills cascade in
``engines.execution.fills_handler`` then drives the closing structure to
``fully_filled``, at which point ``finalise_position_close`` flips the
parent ``trade_positions`` row to ``state='closed'``.

Phase toggle (cf. spec §11) :
    1. Read-only            — alerts persisted, no auto-close (default)
    2. Auto-hedge only      — delta hedges fire automatically, exit alerts
                              still need manual click
    3. Auto-hedge + auto-exit — EXIT alerts trigger this orchestrator

Selected via env ``EXIT_AUTO_EXECUTE_ENABLED=true`` or risk_limits row of
the same name.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.positions.closing import EntryLegSnapshot, build_closing_legs
from core.products import product_label_from_symbol
from persistence.models import (
    BookedPosition,
    ExitAlert,
    StructureOrder,
    TradeEvent,
    TradeStructure,
)
from persistence.reservation import recompute_reservation

logger = logging.getLogger(__name__)


def auto_execute_enabled() -> bool:
    """Phase 3 toggle. False → exit alerts stay manual-review only."""
    return os.environ.get("EXIT_AUTO_EXECUTE_ENABLED", "false").lower() in (
        "1", "true", "yes",
    )


async def initiate_position_close(
    *,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    position_id: int,
    reason: str,
    exit_alert_id: int | None = None,
    execution_mode: str = "live",
) -> dict[str, Any]:
    """Create the closing structure and POST to execution-engine.

    Returns a summary including the new closing_structure_id.
    """
    sm = sessionmaker_factory
    async with sm() as db:
        pos = await db.get(BookedPosition, position_id)
        if pos is None:
            raise ValueError(f"position {position_id} not found")
        if pos.state != "open":
            return {
                "position_id": position_id,
                "skipped": True,
                "state": pos.state,
            }

        entries = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.structure_id == pos.structure_id)
            .where(StructureOrder.order_role == "entry")
            .order_by(StructureOrder.leg_idx)
        )).scalars().all()
        snapshots = [
            EntryLegSnapshot(
                leg_idx=e.leg_idx, contract_type=e.contract_type,
                contract_strike=float(e.contract_strike or 0.0),
                contract_expiry=e.contract_expiry,
                contract_symbol=e.contract_symbol,
                contract_exchange=e.contract_exchange,
                contract_currency=e.contract_currency,
                side=e.side, qty_filled=int(e.qty_filled or 0),
                preview_iv_pct=e.preview_iv_pct,
                preview_price=e.preview_price,
            )
            for e in entries
        ]
        closing_legs = build_closing_legs(snapshots)

        parent_struct = await db.get(TradeStructure, pos.structure_id)
        # Build a new TradeStructure for the close — links back to position
        # via ExitAlert.closing_structure_id (set below).
        closing_struct_type = (
            parent_struct.structure_type if parent_struct else "close"
        )
        closing_struct = TradeStructure(
            structure_type=closing_struct_type,
            product_label=product_label_from_symbol(None, closing_struct_type),
            reference_tenor=(parent_struct.reference_tenor if parent_struct else "3M"),
            expiry_date=(parent_struct.expiry_date if parent_struct else None),
            base_qty=1,
            state="submitted",
            execution_mode=execution_mode,
        )
        db.add(closing_struct)
        await db.flush()

        # OMS P2 : link each closing leg to the entry leg it closes (by leg_idx)
        # so the reservation ledger can attribute reserved_qty to that leg (I5).
        entry_by_leg = {int(e.leg_idx): int(e.id) for e in entries}
        touched_entries: set[int] = set()
        for leg in closing_legs:
            entry_id = entry_by_leg.get(int(leg.leg_idx))
            db.add(StructureOrder(
                structure_id=closing_struct.id,
                leg_idx=leg.leg_idx, order_role="closing",
                contract_symbol=leg.contract_symbol,
                contract_type=leg.contract_type,
                contract_strike=leg.contract_strike,
                contract_expiry=leg.contract_expiry,
                contract_exchange=leg.contract_exchange,
                contract_currency=leg.contract_currency,
                side=leg.side, qty=leg.qty,
                order_type="LMT",
                limit_price=leg.preview_price,
                preview_iv_pct=leg.preview_iv_pct,
                preview_price=leg.preview_price,
                state="pending",
                closes_order_id=entry_id,
            ))
            if entry_id is not None:
                touched_entries.add(entry_id)
        await db.flush()  # closing orders queryable before we fold the reservation
        for eid in touched_entries:
            await recompute_reservation(db, entry_order_id=eid)

        # OpenPosition transitions to 'closing' immediately ; final flip to
        # 'closed' happens inside the fills cascade.
        pos.state = "closing"

        if exit_alert_id is not None:
            alert = await db.get(ExitAlert, exit_alert_id)
            if alert is not None:
                alert.auto_executed = True
                alert.execution_status = "in_progress"
                alert.closing_structure_id = closing_struct.id

        db.add(TradeEvent(
            structure_id=closing_struct.id,
            position_id=position_id,
            event_type="position_close_initiated",
            severity="info",
            description=f"close position {position_id} : {reason[:200]}",
            payload={
                "position_id": position_id,
                "parent_structure_id": pos.structure_id,
                "n_legs": len(closing_legs),
                "execution_mode": execution_mode,
            },
        ))
        await db.commit()
        closing_structure_id = closing_struct.id

    # Fire to execution-engine (live only). Mock mode would simulate fills
    # locally — we don't bother in V1 since closing flow is primarily a
    # production concern.
    submitted: dict[str, Any] = {"deferred": True}
    if execution_mode == "live":
        try:
            import httpx
            base = os.environ.get(
                "EXECUTION_ENGINE_URL", "http://execution-engine:8001",
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{base.rstrip('/')}/internal/structure/submit",
                    json={"structure_id": closing_structure_id},
                )
            submitted = {"status": resp.status_code, "body": resp.json()}
        except Exception as e:
            logger.exception("close_dispatch_failed pos=%s", position_id)
            submitted = {"error": str(e)[:300]}

    return {
        "position_id": position_id,
        "closing_structure_id": closing_structure_id,
        "n_legs": len(closing_legs),
        "execution_engine": submitted,
    }


# Note: ``finalise_position_close`` lives in
# ``engines.execution.position_close_finaliser`` because it is invoked from
# the fills cascade (engines may not import api per .importlinter).
