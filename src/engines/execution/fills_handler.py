"""ib_insync event handlers — order status + execution callbacks.

Wired by ``live_submit.attach_fill_handlers`` to a freshly-placed Trade.
The Trade object exposes two relevant events :
  * ``trade.statusEvent`` — fired on each `orderStatus` change (Submitted,
    PreSubmitted, Filled, Cancelled, ApiCancelled, Inactive, Rejected, ...).
  * ``trade.fillEvent`` — fired on each ``Execution`` (partial or full).

Persistence uses a fresh ``AsyncSession`` per event because ib_insync events
fire from the asyncio loop and we don't want long-lived sessions across
arbitrary callback boundaries.

Cascade on full fill :
  * ``_on_execution`` writes the fill row + recomputes `structure_orders`
    aggregates with ``update_order_aggregates``.
  * If the order tips to ``fully_filled`` we mark it and call
    ``maybe_complete_structure`` which checks if ALL entry orders of the
    parent structure are filled — if so it creates the ``trade_positions``
    row + flips the structure to ``fully_filled`` (cf. STEP4 §7.2 step 5).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bus.publisher import publish_order_event
from core.execution.fills import (
    FillEvent,
    apply_fill_idempotent,
    update_order_aggregates,
)
from engines.execution.position_projector import rebuild_for_order
from engines.execution.redis_state import get_client as _get_redis
from persistence.models import (
    BookedPosition,
    StructureFill,
    StructureOrder,
    TradeEvent,
    TradeStructure,
)
from persistence.reservation import recompute_reservation

logger = logging.getLogger(__name__)


# ib_insync OrderStatus.status values we care about → DB state
_STATUS_TO_STATE = {
    "Submitted": "submitted",
    "PreSubmitted": "submitted",
    "PendingSubmit": "submitted",
    "ApiPending": "submitted",
    "Filled": "filled",
    "Cancelled": "cancelled",
    "ApiCancelled": "cancelled",
    "Inactive": "rejected",
}


def _map_status(ib_status: str) -> str | None:
    """Return our DB state for an IB status string, or None to ignore."""
    return _STATUS_TO_STATE.get(ib_status)


# --------------------------------------------------------------------------
# Event handlers
# --------------------------------------------------------------------------

def attach_fill_handlers(
    *, trade: Any, order_id: int,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Wire the two event callbacks. Called once per placed Trade."""
    trade.statusEvent += lambda t: asyncio.create_task(
        _on_order_status(t, order_id, sessionmaker_factory)
    )
    trade.fillEvent += lambda t, fill: asyncio.create_task(
        _on_execution(t, fill, order_id, sessionmaker_factory)
    )


async def _on_order_status(
    trade: Any, order_id: int,
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """Persist state transitions on ``structure_orders`` + audit log."""
    try:
        status = trade.orderStatus.status
        new_state = _map_status(status)
        if new_state is None:
            return
        async with sm() as db:
            order = await db.get(StructureOrder, order_id)
            if order is None:
                logger.warning("order_status_event_for_missing_order id=%s", order_id)
                return
            old_state = order.state
            now = datetime.now(UTC)
            ws_event: dict | None = None
            if new_state == "submitted" and order.state == "pending":
                order.state = "submitted"
                order.submitted_at = order.submitted_at or now
                _audit(db, order, "order_acknowledged", "info",
                       f"ib status={status}", {"old_state": old_state})
                ws_event = {"event_type": "order_acknowledged"}
            elif new_state == "submitted" and order.state == "submitted":
                if order.acknowledged_at is None:
                    order.acknowledged_at = now
                    _audit(db, order, "order_acknowledged", "info",
                           f"ib status={status}", {})
                    ws_event = {"event_type": "order_acknowledged"}
            elif new_state == "rejected":
                order.state = "rejected"
                order.rejected_at = now
                order.rejection_text = (status or "")[:300]
                _audit(db, order, "order_rejected", "error",
                       f"ib status={status}", {"old_state": old_state})
                ws_event = {"event_type": "order_rejected"}
            elif new_state == "cancelled":
                order.state = "cancelled"
                _audit(db, order, "order_cancelled", "warning",
                       f"ib status={status}", {"old_state": old_state})
                ws_event = {"event_type": "order_cancelled"}
            # Filled is handled in _on_execution after fill aggregates ; we
            # don't transition here to avoid races.
            if (
                order.state in ("rejected", "cancelled")
                and order.closes_order_id is not None
            ):
                # A dead close releases its residual reservation (I5, spec §8).
                await recompute_reservation(db, leg_order_id=order.closes_order_id)
            await db.commit()

            if ws_event is not None:
                ws_event.update({
                    "order_id": order.id, "leg_idx": order.leg_idx,
                    "state": order.state, "ib_status": status,
                })
                await _publish_order_safe(order.structure_id, ws_event)
    except Exception:
        logger.exception("on_order_status_failed order_id=%s", order_id)


async def _on_execution(
    trade: Any, fill: Any, order_id: int,
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """Persist a single fill (idempotent on ib_execution_id) + cascade."""
    try:
        exec_id = str(fill.execution.execId)
        qty = int(fill.execution.shares)
        price = float(fill.execution.price)
        commission = float(getattr(fill.commissionReport, "commission", 0.0) or 0.0)
        ts = getattr(fill.execution, "time", None) or datetime.now(UTC)
        side = str(fill.execution.side).upper()  # 'BOT' / 'SLD' on IB
        side = "BUY" if side in ("BOT", "BUY") else "SELL"
        exchange = getattr(fill.execution, "exchange", None)

        async with sm() as db:
            # Idempotence : skip if ib_execution_id already persisted.
            existing_ids = (await db.execute(
                select(StructureFill.ib_execution_id)
                .where(StructureFill.order_id == order_id)
            )).scalars().all()
            if not apply_fill_idempotent(existing_ids, exec_id):
                logger.info("fill_skipped_duplicate exec_id=%s", exec_id)
                return

            db.add(StructureFill(
                order_id=order_id,
                ib_execution_id=exec_id,
                timestamp=ts if isinstance(ts, datetime) else datetime.now(UTC),
                qty_filled=qty,
                fill_price=price,
                commission_usd=commission,
                exchange=exchange,
                side=side,
                spot_at_fill=None,   # populated when market_data Redis cache lands (Passe C)
                bid_at_fill=None,
                ask_at_fill=None,
            ))
            await db.flush()

            # Recompute aggregates from the full fill stream for this order.
            order = await db.get(StructureOrder, order_id)
            if order is None:
                await db.commit()
                return
            all_fills = (await db.execute(
                select(StructureFill).where(StructureFill.order_id == order_id)
            )).scalars().all()
            agg = update_order_aggregates(
                [
                    FillEvent(
                        ib_execution_id=f.ib_execution_id, qty_filled=f.qty_filled,
                        fill_price=f.fill_price, commission_usd=f.commission_usd,
                    )
                    for f in all_fills
                ],
                target_qty=order.qty, side=order.side,
                preview_price=order.preview_price,
            )
            order.qty_filled = agg.qty_filled
            order.avg_fill_price = agg.avg_fill_price
            order.total_commission_usd = agg.total_commission_usd
            order.slippage_per_contract = agg.slippage_per_contract
            order.total_slippage_usd = agg.total_slippage_usd
            full_fill = False
            if agg.fully_filled and order.state != "filled":
                order.state = "filled"
                order.fully_filled_at = datetime.now(UTC)
                _audit(db, order, "order_filled", "info",
                       f"qty={agg.qty_filled} avg={agg.avg_fill_price}",
                       {"slippage_per_contract": agg.slippage_per_contract})
                full_fill = True
            elif not agg.fully_filled:
                order.state = "partially_filled"
            structure_id = order.structure_id
            await db.commit()

            await _publish_order_safe(structure_id, {
                "event_type": "order_filled" if full_fill else "order_partial_fill",
                "order_id": order_id,
                "qty_filled": agg.qty_filled,
                "avg_fill_price": agg.avg_fill_price,
                "state": order.state,
            })

            # Forward projection (invariant I3) : fold the affected leg's book
            # position from its fill log — entry leg, or the leg a closing
            # order points at via closes_order_id.
            await rebuild_for_order(sm, order_id)

            # Outside the same transaction : structure cascade may write
            # trade_positions row.
            if agg.fully_filled:
                await maybe_complete_structure(sm, structure_id)
    except Exception:
        logger.exception("on_execution_failed order_id=%s", order_id)


# --------------------------------------------------------------------------
# Structure cascade — fully_filled → trade_positions
# --------------------------------------------------------------------------

async def maybe_complete_structure(
    sm: async_sessionmaker[AsyncSession], structure_id: int,
) -> None:
    """If all orders of a single role are filled, finalise the structure.

    * Entry structure  → flip to fully_filled + create the trade_positions row.
    * Closing structure → flip to fully_filled + delegate to
      ``api.orchestration.position_close.finalise_position_close`` (which
      stamps ``trade_positions.closed_at`` + P&L).
    """
    async with sm() as db:
        struct = await db.get(TradeStructure, structure_id)
        if struct is None or struct.state == "fully_filled":
            return
        # Inspect the dominant role on this structure.
        all_orders = (await db.execute(
            select(StructureOrder).where(StructureOrder.structure_id == structure_id)
        )).scalars().all()
        if not all_orders:
            return
        roles = {o.order_role for o in all_orders}
        is_closing_only = roles == {"closing"}
        primary_role = "closing" if is_closing_only else "entry"
        orders = [o for o in all_orders if o.order_role == primary_role]
        if not orders:
            return
        if not all(o.state == "filled" for o in orders):
            return

        # Cross-leg aggregates : signed premium, total commission, total slippage.
        total_premium = 0.0
        total_commission = 0.0
        total_slippage = 0.0
        first_fill_at: datetime | None = None
        last_fill_at: datetime | None = None
        for o in orders:
            sign = +1 if o.side == "BUY" else -1
            avg = float(o.avg_fill_price or 0.0)
            total_premium += sign * avg * float(o.qty_filled or 0)
            total_commission += float(o.total_commission_usd or 0.0)
            total_slippage += float(o.total_slippage_usd or 0.0)
            if o.fully_filled_at:
                first_fill_at = (
                    o.fully_filled_at if first_fill_at is None
                    else min(first_fill_at, o.fully_filled_at)
                )
                last_fill_at = (
                    o.fully_filled_at if last_fill_at is None
                    else max(last_fill_at, o.fully_filled_at)
                )

        now = datetime.now(UTC)
        struct.state = "fully_filled"
        struct.first_fill_at = first_fill_at or now
        struct.fully_filled_at = last_fill_at or now
        struct.total_premium_paid_usd = round(total_premium, 4)
        struct.total_slippage_usd = round(total_slippage, 4)
        struct.total_commission_usd = round(total_commission, 2)
        struct.total_entry_cost_usd = round(total_slippage + total_commission, 2)

        if primary_role == "entry":
            # Idempotent : skip if a position row already exists.
            existing = (await db.execute(
                select(func.count())
                .select_from(BookedPosition)
                .where(BookedPosition.structure_id == structure_id)
            )).scalar_one()
            if existing == 0:
                db.add(BookedPosition(
                    structure_id=structure_id,
                    opened_at=now,
                    entry_premium_usd=struct.total_premium_paid_usd or 0.0,
                    entry_total_cost_usd=struct.total_entry_cost_usd or 0.0,
                    state="open",
                ))
            db.add(TradeEvent(
                structure_id=structure_id,
                event_type="structure_filled", severity="info",
                description="all entry legs filled, position created",
                payload={"premium_usd": struct.total_premium_paid_usd},
            ))
            await db.commit()
            await _publish_order_safe(structure_id, {
                "event_type": "structure_filled",
                "premium_usd": struct.total_premium_paid_usd,
            })
        else:
            # Closing structure : audit the fully_filled then delegate to
            # api.orchestration.position_close which flips trade_positions.
            db.add(TradeEvent(
                structure_id=structure_id,
                event_type="closing_structure_filled", severity="info",
                description="all closing legs filled",
                payload={"premium_usd": struct.total_premium_paid_usd},
            ))
            await db.commit()
            await _publish_order_safe(structure_id, {
                "event_type": "closing_structure_filled",
                "premium_usd": struct.total_premium_paid_usd,
            })
            try:
                from engines.execution.position_close_finaliser import (
                    finalise_position_close,
                )
                await finalise_position_close(
                    sessionmaker_factory=sm, closing_structure_id=structure_id,
                )
            except Exception:
                logger.exception(
                    "finalise_position_close_failed structure_id=%s", structure_id,
                )


# --------------------------------------------------------------------------
# Audit-log helper
# --------------------------------------------------------------------------

def _audit(
    db: AsyncSession, order: StructureOrder, event_type: str,
    severity: str, message: str, payload: dict[str, Any],
) -> None:
    db.add(TradeEvent(
        structure_id=order.structure_id,
        order_id=order.id,
        event_type=event_type,
        severity=severity,
        description=message[:500],
        payload=payload,
    ))


async def _publish_order_safe(structure_id: int, event: dict[str, Any]) -> None:
    """Best-effort PUBLISH on ``orders:<structure_id>`` — silent on Redis down."""
    redis = _get_redis()
    if redis is None:
        return
    try:
        await publish_order_event(redis, structure_id, event)
    except Exception:
        logger.warning("publish_order_event_failed", exc_info=True)
