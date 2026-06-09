"""Live execution path — replaces the mock submit when execution_mode='live'.

API flow
--------
1. ``api.routers.trade.submit_preview`` (execution_mode='live') :
     - persists ``trade_structures`` (state='submitted') + ``structure_orders``
       (state='pending', limit_price already computed)
     - then HTTP-POSTs ``execution-engine:8001/internal/structure/submit``
       with the new ``structure_id``.
2. This module reads the orders, places them via ib_insync, attaches event
   handlers (``_on_order_status`` / ``_on_execution``), and returns a stub
   summary. Persistence of fills + cascade to ``trade_positions`` happens
   asynchronously inside the event callbacks (cf. ``fills_handler.py``).

Combo support
-------------
``can_use_combo`` is checked. If true → TODO (BAG) ; for V1 we still issue
separate orders but log it. Spec §14 limitation 5 acknowledges this.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.contract_builder import (
    build_contract_kwargs,
    build_order_kwargs,
    can_use_combo,
)
from engines.execution.fills_handler import attach_fill_handlers
from persistence.models import (
    ExecutionAuditLog,
    StructureOrder,
    TradeStructure,
)

logger = logging.getLogger(__name__)


class LiveSubmitError(RuntimeError):
    """Raised when live submit cannot proceed (IB down, contract unqualified)."""


async def submit_structure_live(
    *,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,                  # OrderExecutor
    structure_id: int,
) -> dict[str, Any]:
    """Place all entry orders of a structure and wire fills handlers.

    Returns a summary dict — final state arrives via events, not via this call.
    """
    if not executor.is_connected():
        raise LiveSubmitError("IB Gateway not connected")

    from ib_insync import Contract, LimitOrder

    sm = sessionmaker_factory
    async with sm() as db:
        struct = (await db.execute(
            select(TradeStructure).where(TradeStructure.id == structure_id).limit(1)
        )).scalar_one_or_none()
        if struct is None:
            raise LiveSubmitError(f"structure {structure_id} not found")
        orders = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.structure_id == structure_id)
            .where(StructureOrder.order_role == "entry")
            .order_by(StructureOrder.leg_idx)
        )).scalars().all()
        if not orders:
            raise LiveSubmitError(f"structure {structure_id} has no entry orders")

        # Combo detection (informational for V1 — we still issue separate orders).
        legs_meta = [
            {
                "expiry": o.contract_expiry,
                "contract_symbol": o.contract_symbol,
                "contract_exchange": o.contract_exchange,
                "contract_currency": o.contract_currency,
            }
            for o in orders
        ]
        combo_eligible = can_use_combo(legs_meta)
        db.add(ExecutionAuditLog(
            structure_id=structure_id,
            event_type="live_submit_attempt", severity="info",
            message=f"live submit ; combo_eligible={combo_eligible}",
            payload={"n_orders": len(orders), "combo_eligible": combo_eligible},
        ))

        ib = executor._ensure()
        placed: list[dict[str, Any]] = []
        for order in orders:
            if order.contract_strike is None or order.contract_expiry is None:
                raise LiveSubmitError(
                    f"order {order.id} missing strike/expiry — cannot build contract"
                )
            if order.limit_price is None:
                raise LiveSubmitError(f"order {order.id} missing limit_price")

            ck = build_contract_kwargs(
                contract_type=order.contract_type,
                expiry=order.contract_expiry,
                strike=float(order.contract_strike),
                symbol=order.contract_symbol,
                exchange=order.contract_exchange,
                currency=order.contract_currency,
            )
            contract = Contract(**ck)
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                raise LiveSubmitError(f"contract not qualified for order {order.id}")
            contract = qualified[0]

            ok = build_order_kwargs(
                side=order.side, qty=order.qty,
                limit_price=float(order.limit_price),
                time_in_force=order.time_in_force or "DAY",
            )
            ib_order = LimitOrder(ok["action"], ok["totalQuantity"], ok["lmtPrice"])
            ib_order.tif = ok["tif"]

            trade = ib.placeOrder(contract, ib_order)
            attach_fill_handlers(
                trade=trade, order_id=order.id, sessionmaker_factory=sm,
            )

            order.ib_order_id = str(trade.order.orderId)
            order.ib_perm_id = str(trade.order.permId) if trade.order.permId else None
            order.state = "submitted"
            order.submitted_at = datetime.now(UTC)
            placed.append({
                "leg_idx": order.leg_idx,
                "order_id": order.id,
                "ib_order_id": order.ib_order_id,
            })
        await db.commit()

    return {
        "structure_id": structure_id,
        "n_orders_placed": len(placed),
        "combo_eligible": combo_eligible,
        "orders": placed,
    }
