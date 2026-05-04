"""Execute a `RollbackPlan` (pure decision from core.execution.rollback) by
issuing the IB cancel + opposite-side unwind orders, then audit-logging.

Spec : ``docs/vol_trading_pca/specs/STEP4_EXECUTION.md`` §7.3.

Caller patterns :
    plan = decide_rollback(order_states)
    if plan.is_noop():
        return
    await run_rollback(sm, executor, structure_id, plan)
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.rollback import OrderState, RollbackPlan, decide_rollback
from persistence.models import ExecutionAuditLog, StructureOrder

logger = logging.getLogger(__name__)


async def load_order_states(
    db: AsyncSession, structure_id: int,
) -> tuple[list[OrderState], dict[int, StructureOrder]]:
    """Return (pure-state-list, leg_idx → ORM row) pair for the structure."""
    rows = (await db.execute(
        select(StructureOrder)
        .where(StructureOrder.structure_id == structure_id)
        .where(StructureOrder.order_role == "entry")
    )).scalars().all()
    states = [
        OrderState(
            leg_idx=r.leg_idx, state=r.state, side=r.side,
            qty=r.qty, qty_filled=r.qty_filled or 0,
        )
        for r in rows
    ]
    by_leg = {r.leg_idx: r for r in rows}
    return states, by_leg


async def run_rollback(
    *,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,                 # OrderExecutor
    structure_id: int,
    plan: RollbackPlan | None = None,
) -> dict[str, Any]:
    """Execute cancel + unwind. Plan is recomputed if not passed.

    Returns a small report. Mutations to ``structure_orders`` are committed
    inside this function ; subsequent fills events update the unwind orders.
    """
    if not executor.is_connected():
        raise RuntimeError("IB Gateway not connected — cannot rollback")

    from ib_insync import LimitOrder

    sm = sessionmaker_factory
    cancelled: list[int] = []
    unwound: list[dict[str, Any]] = []

    async with sm() as db:
        states, by_leg = await load_order_states(db, structure_id)
        if plan is None:
            plan = decide_rollback(states)
        if plan.is_noop():
            return {"cancelled": [], "unwound": [], "noop": True}

        ib = executor._ensure()

        # 1. Cancel — non-blocking IB call.
        for action in plan.cancels:
            order = by_leg.get(action.leg_idx)
            if order is None or not order.ib_order_id:
                continue
            ib_order_id_int = _safe_int(order.ib_order_id)
            if ib_order_id_int is None:
                continue
            for trade in ib.openTrades():
                if trade.order.orderId == ib_order_id_int:
                    ib.cancelOrder(trade.order)
                    break
            db.add(ExecutionAuditLog(
                structure_id=structure_id, order_id=order.id,
                event_type="order_cancelled", severity="warning",
                message=f"rollback cancel leg={action.leg_idx}",
                payload={"ib_order_id": order.ib_order_id},
            ))
            cancelled.append(order.id)

        # 2. Unwind — opposite-side LMT placed at preview_price (caller
        #    later recomputes a tighter limit if needed). Result is a NEW
        #    order row with order_role='unwind' so reporting is clean.
        for action in plan.unwinds:
            entry = by_leg.get(action.leg_idx)
            if entry is None:
                continue
            unwind_limit = float(entry.preview_price or entry.limit_price or 0.0)
            if unwind_limit <= 0:
                logger.warning(
                    "unwind_skipped_no_price structure_id=%s leg=%s",
                    structure_id, action.leg_idx,
                )
                continue

            unwind_order = StructureOrder(
                structure_id=structure_id, leg_idx=action.leg_idx,
                order_role="unwind",
                contract_symbol=entry.contract_symbol,
                contract_type=entry.contract_type,
                contract_expiry=entry.contract_expiry,
                contract_strike=entry.contract_strike,
                contract_exchange=entry.contract_exchange,
                contract_currency=entry.contract_currency,
                side=action.side, qty=action.qty,
                order_type="LMT", limit_price=unwind_limit,
                state="pending",
            )
            db.add(unwind_order)
            await db.flush()

            # Place via IB. Contract must already be qualified — we reuse
            # the entry's qualification via permId lookup if available, else
            # rebuild and re-qualify.
            from ib_insync import Contract

            from core.execution.contract_builder import build_contract_kwargs

            ck = build_contract_kwargs(
                contract_type=entry.contract_type,
                expiry=entry.contract_expiry,
                strike=float(entry.contract_strike),
                symbol=entry.contract_symbol,
                exchange=entry.contract_exchange,
                currency=entry.contract_currency,
            )
            contract = Contract(**ck)
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                logger.error(
                    "unwind_contract_not_qualified structure=%s leg=%s",
                    structure_id, action.leg_idx,
                )
                continue
            ib_order = LimitOrder(action.side, action.qty, unwind_limit)
            trade = ib.placeOrder(qualified[0], ib_order)
            unwind_order.ib_order_id = str(trade.order.orderId)
            unwind_order.state = "submitted"

            # Wire fills handler so partial fills on the unwind also write rows.
            from engines.execution.fills_handler import attach_fill_handlers
            attach_fill_handlers(
                trade=trade, order_id=unwind_order.id, sessionmaker_factory=sm,
            )

            db.add(ExecutionAuditLog(
                structure_id=structure_id, order_id=unwind_order.id,
                event_type="unwind_order_created", severity="warning",
                message=(
                    f"rollback unwind leg={action.leg_idx} "
                    f"side={action.side} qty={action.qty}"
                ),
                payload={
                    "original_leg": action.leg_idx,
                    "ib_order_id": unwind_order.ib_order_id,
                },
            ))
            unwound.append({
                "leg_idx": action.leg_idx,
                "side": action.side, "qty": action.qty,
                "ib_order_id": unwind_order.ib_order_id,
            })

        await db.commit()

    return {"cancelled": cancelled, "unwound": unwound, "noop": False}


def _safe_int(s: str | None) -> int | None:
    if s is None:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None
