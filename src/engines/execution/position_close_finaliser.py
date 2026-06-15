"""Stamp ``trade_positions.state='closed'`` once a closing structure
``fully_filled``.

Owned by execution-engine because the trigger is a fills callback (cf.
``fills_handler.maybe_complete_structure``). The orchestration that
*initiates* the close lives in api/orchestration ; both share the pure
helpers in ``core.positions.closing``.

Architecture note : engines must not import api (cf. .importlinter), so
the finalisation cannot live alongside ``initiate_position_close`` in
api/orchestration. Same DB writes — different module.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from persistence.models import (
    BookedPosition,
    ExecutionAuditLog,
    ExitAlert,
    HedgeOrder,
    TradeStructure,
)

logger = logging.getLogger(__name__)


async def finalise_position_close(
    *,
    sessionmaker_factory: async_sessionmaker,
    closing_structure_id: int,
) -> bool:
    """Idempotent finaliser. Returns True when the position was actually closed.

    Called from ``fills_handler.maybe_complete_structure`` when a closing
    structure tips to ``fully_filled``. Computes :
      * gross_pnl_usd = -exit_premium - entry_premium  (both sign-aware)
      * net_pnl_usd   = gross - entry_total_cost - exit_total_cost - hedge_cost
    """
    sm = sessionmaker_factory
    async with sm() as db:
        closing = await db.get(TradeStructure, closing_structure_id)
        if closing is None or closing.state != "fully_filled":
            return False
        alert = (await db.execute(
            select(ExitAlert)
            .where(ExitAlert.closing_structure_id == closing_structure_id)
            .limit(1)
        )).scalar_one_or_none()
        if alert is None:
            return False
        pos = await db.get(BookedPosition, alert.position_id)
        if pos is None or pos.state == "closed":
            return False

        now = datetime.now(UTC)
        pos.state = "closed"
        pos.closed_at = now
        pos.close_reason = (alert.rule_triggered or "")[:80]
        pos.exit_premium_usd = float(closing.total_premium_paid_usd or 0.0)
        pos.exit_total_cost_usd = float(closing.total_entry_cost_usd or 0.0)

        gross = (-pos.exit_premium_usd) - pos.entry_premium_usd
        rows = (await db.execute(
            select(HedgeOrder)
            .where(HedgeOrder.position_id == pos.id)
            .where(HedgeOrder.state == "filled")
        )).scalars().all()
        hedge_cost = sum(float(r.total_cost_usd or 0.0) for r in rows)
        net = (
            gross
            - (pos.entry_total_cost_usd or 0.0)
            - pos.exit_total_cost_usd
            - hedge_cost
        )
        pos.gross_pnl_usd = round(gross, 2)
        pos.net_pnl_usd = round(net, 2)

        if alert.execution_status == "in_progress":
            alert.execution_status = "done"

        db.add(ExecutionAuditLog(
            structure_id=closing_structure_id,
            event_type="position_closed", severity="info",
            message=f"position {pos.id} closed",
            payload={
                "position_id": pos.id,
                "gross_pnl_usd": pos.gross_pnl_usd,
                "net_pnl_usd": pos.net_pnl_usd,
                "hedge_cost_usd": hedge_cost,
            },
        ))
        await db.commit()
    return True
