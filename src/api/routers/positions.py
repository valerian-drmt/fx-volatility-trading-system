"""Step 5 — Active positions monitoring API.

GET  /api/v1/positions/active                 list open positions + last MTM snapshot
GET  /api/v1/positions/{id}                   detailed view of one position
GET  /api/v1/positions/{id}/mtm-history       mtm series for charting
GET  /api/v1/positions/{id}/alerts            exit alerts log
GET  /api/v1/positions/{id}/hedges            hedge orders log
GET  /api/v1/positions/{id}/signal-tracking   signal vs entry trail
POST /api/v1/positions/{id}/close-manual      mark for manual close (mock — Step 5 phase 1)
POST /api/v1/positions/monitor/run-once       trigger 1 cycle on demand (dev/debug)
GET  /api/v1/positions/aggregate              greeks aggregate across open positions
GET  /api/v1/positions/exit-rules-config      hot-reload config visibility
GET  /api/v1/positions/delta-hedge-config     hot-reload config visibility
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session
from api.orchestration.position_monitor import build_position_monitor_scheduler
from persistence.models import (
    DeltaHedgeConfig,
    ExitAlert,
    ExitRulesConfig,
    HedgeOrder,
    PositionMtmHistory,
    PositionSignalTracking,
    TradePosition,
    TradeStructure,
)

router = APIRouter(prefix="/api/v1/positions", tags=["positions"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


def _serialize_position(pos: TradePosition, struct: TradeStructure | None,
                        latest_mtm: PositionMtmHistory | None) -> dict[str, Any]:
    return {
        "id": pos.id, "structure_id": pos.structure_id,
        "structure_type": struct.structure_type if struct else None,
        "reference_tenor": struct.reference_tenor if struct else None,
        "expiry_date": struct.expiry_date.isoformat() if struct and struct.expiry_date else None,
        "triggering_pc": struct.triggering_pc if struct else None,
        "armed_z_score": float(struct.armed_z_score) if struct and struct.armed_z_score is not None else None,
        "armed_signal_label": struct.armed_signal_label if struct else None,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "state": pos.state,
        "entry_premium_usd": pos.entry_premium_usd,
        "entry_total_cost_usd": pos.entry_total_cost_usd,
        "entry_vega_usd_per_volpt": pos.entry_vega_usd_per_volpt,
        "entry_gamma_usd_per_pip2": pos.entry_gamma_usd_per_pip2,
        "entry_theta_usd_per_day": pos.entry_theta_usd_per_day,
        "entry_spot": pos.entry_spot, "entry_iv_avg": pos.entry_iv_avg,
        "current_pnl_gross_usd": latest_mtm.current_pnl_gross_usd if latest_mtm else None,
        "current_pnl_net_usd": latest_mtm.current_pnl_net_usd if latest_mtm else None,
        "vega_pnl_usd": latest_mtm.vega_pnl_usd if latest_mtm else None,
        "gamma_pnl_usd": latest_mtm.gamma_pnl_usd if latest_mtm else None,
        "theta_pnl_usd": latest_mtm.theta_pnl_usd if latest_mtm else None,
        "current_vega_usd_per_volpt": latest_mtm.current_vega_usd_per_volpt if latest_mtm else None,
        "current_delta_unhedged": latest_mtm.current_delta_unhedged if latest_mtm else None,
        "last_mtm_at": latest_mtm.timestamp.isoformat() if latest_mtm else None,
    }


@router.get("/active")
async def list_active(db: DbDep) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(TradePosition).where(TradePosition.state == "open")
        .order_by(desc(TradePosition.opened_at))
    )).scalars().all()
    out: list[dict[str, Any]] = []
    for pos in rows:
        struct = (await db.execute(
            select(TradeStructure).where(TradeStructure.id == pos.structure_id).limit(1)
        )).scalar_one_or_none()
        latest = (await db.execute(
            select(PositionMtmHistory).where(PositionMtmHistory.position_id == pos.id)
            .order_by(desc(PositionMtmHistory.timestamp)).limit(1)
        )).scalar_one_or_none()
        out.append(_serialize_position(pos, struct, latest))
    return out


@router.get("/aggregate")
async def aggregate_greeks(db: DbDep) -> dict[str, Any]:
    """Sum of current greeks across all open positions (for Panel 4 zone B)."""
    rows = (await db.execute(
        select(TradePosition).where(TradePosition.state == "open")
    )).scalars().all()
    total_vega = total_gamma = total_theta = total_delta = 0.0
    n = 0
    for pos in rows:
        latest = (await db.execute(
            select(PositionMtmHistory).where(PositionMtmHistory.position_id == pos.id)
            .order_by(desc(PositionMtmHistory.timestamp)).limit(1)
        )).scalar_one_or_none()
        if latest:
            total_vega += latest.current_vega_usd_per_volpt or 0.0
            total_gamma += latest.current_gamma_usd_per_pip2 or 0.0
            total_theta += latest.current_theta_usd_per_day or 0.0
            total_delta += latest.current_delta_unhedged or 0.0
        else:
            total_vega += pos.entry_vega_usd_per_volpt or 0.0
            total_gamma += pos.entry_gamma_usd_per_pip2 or 0.0
            total_theta += pos.entry_theta_usd_per_day or 0.0
        n += 1
    return {
        "n_open_positions": n,
        "total_vega_usd_per_volpt": round(total_vega, 2),
        "total_gamma_usd_per_pip2": round(total_gamma, 4),
        "total_theta_usd_per_day": round(total_theta, 2),
        "total_delta_unhedged": round(total_delta, 4),
    }


@router.get("/exit-rules-config")
async def list_exit_rules_config(db: DbDep) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(ExitRulesConfig).order_by(desc(ExitRulesConfig.priority))
    )).scalars().all()
    return [
        {"rule_name": r.rule_name, "is_active": r.is_active, "priority": r.priority,
         "params": r.params, "description": r.description}
        for r in rows
    ]


@router.get("/delta-hedge-config")
async def list_delta_hedge_config(db: DbDep) -> list[dict[str, Any]]:
    rows = (await db.execute(select(DeltaHedgeConfig))).scalars().all()
    return [
        {"config_name": r.config_name, "config_value": r.config_value,
         "unit": r.unit, "description": r.description}
        for r in rows
    ]


@router.get("/{position_id}")
async def get_position(position_id: int, db: DbDep) -> dict[str, Any]:
    pos = (await db.execute(
        select(TradePosition).where(TradePosition.id == position_id).limit(1)
    )).scalar_one_or_none()
    if pos is None:
        raise HTTPException(404, "position not found")
    struct = (await db.execute(
        select(TradeStructure).where(TradeStructure.id == pos.structure_id).limit(1)
    )).scalar_one_or_none()
    latest = (await db.execute(
        select(PositionMtmHistory).where(PositionMtmHistory.position_id == pos.id)
        .order_by(desc(PositionMtmHistory.timestamp)).limit(1)
    )).scalar_one_or_none()
    return _serialize_position(pos, struct, latest)


@router.get("/{position_id}/mtm-history")
async def mtm_history(
    position_id: int, db: DbDep, hours: int = Query(24, ge=1, le=720),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows = (await db.execute(
        select(PositionMtmHistory).where(PositionMtmHistory.position_id == position_id)
        .where(PositionMtmHistory.timestamp >= cutoff)
        .order_by(PositionMtmHistory.timestamp).limit(limit)
    )).scalars().all()
    return [
        {
            "timestamp": r.timestamp.isoformat(),
            "spot": r.spot, "iv_avg_legs_pct": r.iv_avg_legs_pct,
            "pnl_gross_usd": r.current_pnl_gross_usd, "pnl_net_usd": r.current_pnl_net_usd,
            "vega_pnl_usd": r.vega_pnl_usd, "gamma_pnl_usd": r.gamma_pnl_usd,
            "theta_pnl_usd": r.theta_pnl_usd, "other_pnl_usd": r.other_pnl_usd,
            "vega_usd_per_volpt": r.current_vega_usd_per_volpt,
        } for r in rows
    ]


@router.get("/{position_id}/alerts")
async def position_alerts(
    position_id: int, db: DbDep, limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(ExitAlert).where(ExitAlert.position_id == position_id)
        .order_by(desc(ExitAlert.timestamp)).limit(limit)
    )).scalars().all()
    return [
        {
            "id": r.id, "timestamp": r.timestamp.isoformat(),
            "rule_triggered": r.rule_triggered,
            "action_recommended": r.action_recommended,
            "priority": r.priority, "rule_detail": r.rule_detail,
            "auto_executed": r.auto_executed, "execution_status": r.execution_status,
            "closing_structure_id": r.closing_structure_id,
        } for r in rows
    ]


@router.get("/{position_id}/hedges")
async def position_hedges(
    position_id: int, db: DbDep, limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(HedgeOrder).where(HedgeOrder.position_id == position_id)
        .order_by(desc(HedgeOrder.triggered_at)).limit(limit)
    )).scalars().all()
    return [
        {
            "id": r.id, "triggered_at": r.triggered_at.isoformat(),
            "delta_imbalance_at_trigger": r.delta_imbalance_at_trigger,
            "hedge_qty": r.hedge_qty, "side": r.side,
            "state": r.state,
            "fill_price": r.fill_price, "total_cost_usd": r.total_cost_usd,
            "ib_order_id": r.ib_order_id,
        } for r in rows
    ]


@router.get("/{position_id}/signal-tracking")
async def signal_tracking(
    position_id: int, db: DbDep, limit: int = Query(200, ge=1, le=2000),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(PositionSignalTracking).where(PositionSignalTracking.position_id == position_id)
        .order_by(desc(PositionSignalTracking.timestamp)).limit(limit)
    )).scalars().all()
    return [
        {
            "timestamp": r.timestamp.isoformat(),
            "triggering_pc": r.triggering_pc,
            "current_z_score": r.current_z_score,
            "current_label": r.current_label,
            "entry_z_score": r.entry_z_score,
            "entry_label": r.entry_label,
            "weakening_ratio": r.weakening_ratio,
            "sign_flipped": r.sign_flipped,
            "status": r.status,
        } for r in rows
    ]


@router.post("/{position_id}/close-manual")
async def close_manual(position_id: int, db: DbDep) -> dict[str, Any]:
    """Mark a position for manual close. Step 5 phase 1 = state flip only.

    The actual closing-structure submit + fills will be wired when markets-open
    phase lands (cf. MARKETS_OPEN_TODO.md). For now we record an audit alert.
    """
    pos = (await db.execute(
        select(TradePosition).where(TradePosition.id == position_id).limit(1)
    )).scalar_one_or_none()
    if pos is None:
        raise HTTPException(404, "position not found")
    if pos.state != "open":
        raise HTTPException(400, f"position state is {pos.state}, expected 'open'")

    now = datetime.now(UTC)
    pos.state = "closing"
    pos.state_updated_at = now

    db.add(ExitAlert(
        position_id=position_id, timestamp=now,
        rule_triggered="manual_close",
        action_recommended="EXIT",
        priority=10,
        rule_detail={"source": "user", "phase": "mock"},
        auto_executed=False, execution_status="in_progress",
    ))

    await db.commit()
    return {"position_id": position_id, "state": pos.state, "phase": "mock"}


@router.post("/monitor/run-once")
async def run_monitor_once() -> dict[str, Any]:
    """Trigger one monitoring cycle on demand. Useful from the dev panel.

    The scheduler in api lifespan owns the recurring loop ; this endpoint
    creates an ad-hoc instance and runs ``run_once`` once.
    """
    sched = build_position_monitor_scheduler()
    return await sched.run_once()
