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

from api.dependencies import get_db_session, get_redis_client_or_none
from api.orchestration.position_monitor import build_position_monitor_scheduler
from persistence.models import (
    AppConfigScalar,
    BookedPosition,
    BookedPositionMetricHistory,
    ExitAlert,
    ExitRulesConfig,
    HedgeOrder,
    OpenPosition,
    OpenPositionHistory,
    TradeStructure,
)
from shared.contracts import multiplier_for, parse_local_symbol

router = APIRouter(prefix="/api/v1/positions", tags=["positions"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


def _ib_sync_status(reconciled_at: datetime | None) -> str:
    """fresh < 5 min ; stale 5 min–1 h ; missing ≥ 1 h or never."""
    if reconciled_at is None:
        return "missing"
    age = datetime.now(UTC) - reconciled_at
    if age < timedelta(minutes=5):
        return "fresh"
    if age < timedelta(hours=1):
        return "stale"
    return "missing"


_FUT_MONTH_CODES = "FGHJKMNQUVXZ"  # Jan→Dec, IB convention


def _tenor_bucket(maturity: Any) -> str | None:
    """Closest FX OTC tenor pillar (1W / 2W / 1M / 2M / 3M / 6M / 9M / 1Y / 2Y+).
    Thresholds are midpoints between nominal tenor day counts so a real
    180-day contract (= 6M) lands in the "6M" bucket, not "9M".
    Returns None if maturity is unset.
    """
    if maturity is None:
        return None
    today = datetime.now(UTC).date()
    try:
        days = (maturity - today).days
    except TypeError:
        return None
    if days < 0:
        return "expired"
    if days <= 10:                       # 1W ↔ 2W
        return "1W"
    if days <= 22:                       # 2W ↔ 1M
        return "2W"
    if days <= 45:                       # 1M ↔ 2M
        return "1M"
    if days <= 75:                       # 2M ↔ 3M
        return "2M"
    if days <= 135:                      # 3M ↔ 6M
        return "3M"
    if days <= 225:                      # 6M ↔ 9M
        return "6M"
    if days <= 317:                      # 9M ↔ 1Y
        return "9M"
    if days <= 547:                      # 1Y ↔ 2Y
        return "1Y"
    return "2Y+"

# Trading-class prefix per ``positions.symbol``. Used to rebuild the IB
# ``localSymbol`` for display when not persisted to DB.
_TRADING_CLASS = {
    "EUR": "6E",
    "M6E": "M6E",
}


def _ib_local_symbol(symbol: str | None, maturity: Any) -> str | None:
    """Return the IB-style localSymbol like ``6EM6`` / ``M6EM6`` / ``6EK6``.
    Returns None if the inputs aren't enough to build it."""
    if not symbol or not maturity:
        return None
    cls = _TRADING_CLASS.get(symbol, symbol)
    try:
        month_letter = _FUT_MONTH_CODES[maturity.month - 1]
        year_digit = str(maturity.year)[-1]
    except (AttributeError, IndexError):
        return None
    return f"{cls}{month_letter}{year_digit}"


async def _read_contract_marks(redis: Any) -> dict[int, float]:
    """Read the Redis hash ``contract_marks:EUR`` populated by execution-engine.
    Returns ``{position_id: marketPrice}`` for ALL contract types — the
    futures price for FUT rows, the option premium per unit for OPTIONs.
    """
    if redis is None:
        return {}
    try:
        raw = await redis.hgetall("contract_marks:EUR")
    except Exception:
        return {}
    if not raw:
        return {}
    out: dict[int, float] = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        try:
            out[int(key)] = float(val)
        except (ValueError, TypeError):
            continue
    return out


def _serialize_ib_position(
    pos: OpenPosition, snap: OpenPositionHistory | None, contract_mark: float | None = None,
) -> dict[str, Any]:
    """Serialise an IB-synced row from `positions` table for Step 5 display.

    Same shape as :func:`_serialize_position` but with ``source='ib_live'``
    and most ``trade_position``-specific fields (signal, structure type,
    booking metadata) left null.

    ``contract_mark`` (when provided) is the live IB ``marketPrice`` per
    unit fetched from Redis ; used as the market price for all rows
    (futures price for FUT, option premium for OPT). Falls back to spot
    when missing — useful at boot before the first sync cycle.
    """
    expiry = pos.expiry.isoformat() if pos.expiry else None
    tenor = _tenor_bucket(pos.expiry)
    structure_label = pos.structure or "—"
    spec = parse_local_symbol(pos.structure)
    # All live fields now live on the ``positions`` row itself (UPDATEd by
    # risk-engine each cycle). We read them directly — no snapshot lookup.
    pnl   = float(pos.current_pnl_usd) if pos.current_pnl_usd is not None else None
    delta = float(pos.delta_usd)       if pos.delta_usd       is not None else None
    gamma = float(pos.gamma_usd)       if pos.gamma_usd       is not None else None
    vega  = float(pos.vega_usd)        if pos.vega_usd        is not None else None
    theta = float(pos.theta_usd)       if pos.theta_usd       is not None else None
    iv_v  = float(pos.iv)              if pos.iv              is not None else None
    vanna = float(pos.vanna_usd)       if pos.vanna_usd       is not None else None
    volga = float(pos.volga_usd)       if pos.volga_usd       is not None else None
    pos_mark = float(pos.market_price) if pos.market_price    is not None else None
    qty_abs = float(pos.quantity) if pos.quantity is not None else 0.0
    signed_qty = qty_abs if pos.side == "BUY" else -qty_abs
    mult = float(spec.multiplier) if spec else multiplier_for(None)
    contract_price_entry = (
        float(pos.contract_price_entry) if pos.contract_price_entry is not None else None
    )
    nominal_eur = float(pos.nominal_eur) if pos.nominal_eur is not None else None
    # Market price priority : positions row (UPDATEd by risk-engine each
    # cycle) → Redis hash (boot fallback) → None.
    contract_price_market = pos_mark if pos_mark is not None else contract_mark
    sym = spec.symbol if spec else None
    instr = spec.instrument_type if spec else None
    opt_type = spec.option_type if spec else None
    strike = spec.strike if spec else None
    entry_premium_usd = (
        contract_price_entry * mult if contract_price_entry is not None else None
    )
    return {
        "id": pos.id,
        "source": "ib_live",
        "structure_id": None,
        "structure_type": structure_label,
        "reference_tenor": None,
        "expiry_date": expiry,
        "tenor": tenor,
        "symbol": sym,
        "instrument_type": instr,
        "side": pos.side,
        "quantity": signed_qty,
        "strike": strike,
        "option_type": opt_type,
        "triggering_pc": None,
        "armed_z_score": None,
        "armed_signal_label": None,
        "opened_at": pos.entry_timestamp.isoformat() if pos.entry_timestamp else None,
        "state": "open",  # only OPEN rows live in `positions` after migration 028
        "entry_premium_usd": entry_premium_usd,
        "entry_total_cost_usd": None,
        "entry_vega_usd_per_volpt": None,
        "entry_gamma_usd_per_pip2": None,
        "entry_theta_usd_per_day": None,
        "entry_spot": None, "entry_iv_avg": None,
        "current_pnl_gross_usd": pnl,
        "current_pnl_net_usd": pnl,
        "vega_pnl_usd": None, "gamma_pnl_usd": None, "theta_pnl_usd": None,
        "current_vega_usd_per_volpt": vega,
        "current_gamma_usd_per_pip2": gamma,
        "current_theta_usd_per_day": theta,
        "current_delta_unhedged": delta,
        "last_mtm_at": pos.updated_at.isoformat() if pos.updated_at else None,
        "ib_reconciled_at": pos.updated_at.isoformat() if pos.updated_at else None,
        "ib_qty_total": round(signed_qty) if pos.quantity is not None else None,
        "ib_qty_diff": 0,
        "ib_sync_status": _ib_sync_status(pos.updated_at),
        "nominal_eur": nominal_eur,
        "contract_price_entry": contract_price_entry,
        "contract_price_market": contract_price_market,
        "iv": iv_v,
        "vanna_usd": vanna,
        "volga_usd": volga,
    }


def _serialize_position(pos: BookedPosition, struct: TradeStructure | None,
                        latest_mtm: BookedPositionMetricHistory | None) -> dict[str, Any]:
    return {
        "id": pos.id,
        "source": "booked",
        "structure_id": pos.structure_id,
        "structure_type": struct.structure_type if struct else None,
        "reference_tenor": struct.reference_tenor if struct else None,
        "expiry_date": struct.expiry_date.isoformat() if struct and struct.expiry_date else None,
        "tenor": _tenor_bucket(struct.expiry_date) if struct else None,
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
        "current_gamma_usd_per_pip2": latest_mtm.current_gamma_usd_per_pip2 if latest_mtm else None,
        "current_theta_usd_per_day": latest_mtm.current_theta_usd_per_day if latest_mtm else None,
        "current_delta_unhedged": latest_mtm.current_delta_unhedged if latest_mtm else None,
        "last_mtm_at": latest_mtm.timestamp.isoformat() if latest_mtm else None,
        "ib_reconciled_at": pos.ib_reconciled_at.isoformat() if pos.ib_reconciled_at else None,
        "ib_qty_total": pos.ib_qty_total,
        "ib_qty_diff": pos.ib_qty_diff,
        "ib_sync_status": _ib_sync_status(pos.ib_reconciled_at),
        # Multi-leg structures don't have a single unit price ; left null
        # for booked rows. Computed only on the IB-live serializer.
        "nominal_eur": None,
        "contract_price_entry": None,
        "contract_price_market": None,
    }


@router.get("/active")
async def list_active(db: DbDep) -> list[dict[str, Any]]:
    """Union of booked structures (`trade_positions`) and live IB rows
    (`positions`). The frontend distinguishes via the ``source`` field
    so both lists render in a single Step 5 table."""
    out: list[dict[str, Any]] = []

    booked = (await db.execute(
        select(BookedPosition).where(BookedPosition.state == "open")
        .order_by(desc(BookedPosition.opened_at))
    )).scalars().all()
    for pos in booked:
        struct = (await db.execute(
            select(TradeStructure).where(TradeStructure.id == pos.structure_id).limit(1)
        )).scalar_one_or_none()
        latest = (await db.execute(
            select(BookedPositionMetricHistory).where(BookedPositionMetricHistory.position_id == pos.id)
            .order_by(desc(BookedPositionMetricHistory.timestamp)).limit(1)
        )).scalar_one_or_none()
        out.append(_serialize_position(pos, struct, latest))

    ib_rows = (await db.execute(
        select(OpenPosition).order_by(desc(OpenPosition.entry_timestamp))
    )).scalars().all()
    contract_marks = await _read_contract_marks(get_redis_client_or_none())
    # Live values (mark, P&L, greeks) live directly on each ``OpenPosition`` row
    # since migration 026 — no snapshot lookup needed.
    for ib_pos in ib_rows:
        out.append(_serialize_ib_position(
            ib_pos, snap=None, contract_mark=contract_marks.get(ib_pos.id),
        ))

    return out


@router.get("/aggregate")
async def aggregate_greeks(db: DbDep) -> dict[str, Any]:
    """Sum of current greeks across all open positions (for Panel 4 zone B)."""
    rows = (await db.execute(
        select(BookedPosition).where(BookedPosition.state == "open")
    )).scalars().all()
    total_vega = total_gamma = total_theta = total_delta = 0.0
    n = 0
    for pos in rows:
        latest = (await db.execute(
            select(BookedPositionMetricHistory).where(BookedPositionMetricHistory.position_id == pos.id)
            .order_by(desc(BookedPositionMetricHistory.timestamp)).limit(1)
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
    # delta_hedge_config rows folded into config_scalar with
    # namespace='delta_hedge' (migration 033). Response shape preserved.
    rows = (await db.execute(
        select(AppConfigScalar).where(AppConfigScalar.namespace == "delta_hedge")
    )).scalars().all()
    return [
        {"config_name": r.name, "config_value": r.value,
         "unit": r.unit, "description": r.description}
        for r in rows
    ]


@router.get("/{position_id}")
async def get_position(position_id: int, db: DbDep) -> dict[str, Any]:
    pos = (await db.execute(
        select(BookedPosition).where(BookedPosition.id == position_id).limit(1)
    )).scalar_one_or_none()
    if pos is None:
        raise HTTPException(404, "position not found")
    struct = (await db.execute(
        select(TradeStructure).where(TradeStructure.id == pos.structure_id).limit(1)
    )).scalar_one_or_none()
    latest = (await db.execute(
        select(BookedPositionMetricHistory).where(BookedPositionMetricHistory.position_id == pos.id)
        .order_by(desc(BookedPositionMetricHistory.timestamp)).limit(1)
    )).scalar_one_or_none()
    return _serialize_position(pos, struct, latest)


@router.get("/{position_id}/mtm-history")
async def mtm_history(
    position_id: int, db: DbDep, hours: int = Query(24, ge=1, le=720),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    rows = (await db.execute(
        select(BookedPositionMetricHistory).where(BookedPositionMetricHistory.position_id == position_id)
        .where(BookedPositionMetricHistory.timestamp >= cutoff)
        .order_by(BookedPositionMetricHistory.timestamp).limit(limit)
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
    # Signal-vs-entry trail folded into the mtm rows (migration 039) — read
    # the metric-history rows that carry a triggering_pc (signal-driven only).
    rows = (await db.execute(
        select(BookedPositionMetricHistory)
        .where(BookedPositionMetricHistory.position_id == position_id)
        .where(BookedPositionMetricHistory.triggering_pc.is_not(None))
        .order_by(desc(BookedPositionMetricHistory.timestamp)).limit(limit)
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
            "status": r.signal_status,
        } for r in rows
    ]


@router.post("/{position_id}/close-manual")
async def close_manual(position_id: int, db: DbDep) -> dict[str, Any]:
    """Mark a position for manual close. Step 5 phase 1 = state flip only.

    The actual closing-structure submit + fills will be wired when markets-open
    phase lands (cf. MARKETS_OPEN_TODO.md). For now we record an audit alert.
    """
    pos = (await db.execute(
        select(BookedPosition).where(BookedPosition.id == position_id).limit(1)
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
