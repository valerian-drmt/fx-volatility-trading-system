"""Reconcile stuck orders against the live IB positions (gateway truth).

A combo (BAG) fill is reported by IB on the combo's OWN conId, so the per-leg
fill router can miss it. That's fixed going forward in ``fills_handler``, but past
submissions already lost the fill event and sit forever in ``submitted``.

This backfills them : a ``trade_order`` still in ``submitted`` / ``acknowledged``
whose contract is *actually held at IB* — i.e. present in ``open_position``, which
``position_sync`` mirrors from the IB gateway every cycle — is flipped to
``filled``. A leg with NO live IB position (e.g. a combo leg that filled then went
flat) is left untouched : we never invent a phantom fill. When every entry leg of
a structure is filled, the structure cascade books it (``maybe_complete_structure``).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.fills_handler import maybe_complete_structure
from persistence.models import OpenPosition, StructureOrder, TradeEvent
from shared.contracts import parse_local_symbol

logger = logging.getLogger(__name__)

# Orders in these states are "stuck" (never reached a terminal state).
_STUCK_STATES = ("submitted", "acknowledged", "partially_filled")
# CME EUR FOP strikes sit on a 0.005 grid ; allow a touch more for float noise.
_STRIKE_TOL = 0.006


def _leg_matches_position(order: StructureOrder, pos: OpenPosition) -> bool:
    """True if the live IB ``pos`` is this order leg (same side + contract)."""
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


async def reconcile_stuck_orders(
    sm: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """One reconciliation pass. Returns a small summary for logging / the endpoint."""
    now = datetime.now(UTC)
    filled_ids: list[int] = []
    affected: set[int] = set()

    async with sm() as db:
        stuck = (await db.execute(
            select(StructureOrder).where(StructureOrder.state.in_(_STUCK_STATES))
        )).scalars().all()
        if not stuck:
            return {"stuck": 0, "filled": 0, "structures": []}

        struct_ids = {int(o.structure_id) for o in stuck}
        positions = (await db.execute(
            select(OpenPosition).where(OpenPosition.trade_id.in_(struct_ids))
        )).scalars().all()
        pos_by_struct: dict[int, list[OpenPosition]] = {}
        for p in positions:
            if p.trade_id is not None:
                pos_by_struct.setdefault(int(p.trade_id), []).append(p)

        used: set[int] = set()  # each IB position matches at most one leg
        for o in stuck:
            cands = pos_by_struct.get(int(o.structure_id), [])
            match = next(
                (p for p in cands if p.id not in used and _leg_matches_position(o, p)),
                None,
            )
            if match is None:
                continue
            used.add(match.id)
            o.state = "filled"
            o.qty_filled = int(o.qty)
            if o.avg_fill_price is None:
                o.avg_fill_price = (
                    float(match.market_price) if match.market_price is not None
                    else (float(o.preview_price) if o.preview_price is not None else None)
                )
            if o.ib_local_symbol is None and match.structure:
                o.ib_local_symbol = str(match.structure)[:20]
            o.fully_filled_at = now
            filled_ids.append(int(o.id))
            affected.add(int(o.structure_id))

        for sid in affected:
            db.add(TradeEvent(
                structure_id=sid, event_type="order_reconciled_from_ib", severity="info",
                description="stuck leg(s) matched to a live IB position → marked filled",
                payload={"filled_order_ids": [i for i in filled_ids]},
            ))
        await db.commit()

    for sid in affected:
        try:
            await maybe_complete_structure(sm, sid)
        except Exception:
            logger.exception("reconcile_complete_failed structure_id=%s", sid)

    if filled_ids:
        logger.info(
            "reconcile_stuck_orders filled=%s structures=%s",
            filled_ids, sorted(affected),
        )
    return {"stuck": len(stuck), "filled": len(filled_ids), "structures": sorted(affected)}


async def reconcile_loop(
    sm: async_sessionmaker[AsyncSession], *, interval_s: float = 60.0,
) -> None:
    """Run forever ; reconcile every ``interval_s``. Cancellable via task.cancel()."""
    while True:
        try:
            await reconcile_stuck_orders(sm)
        except Exception:
            logger.exception("reconcile_loop_error")
        await asyncio.sleep(interval_s)
