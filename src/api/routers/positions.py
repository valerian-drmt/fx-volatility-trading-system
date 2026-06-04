"""Step 5 — Active positions monitoring API.

GET  /api/v1/positions/active                 list open positions + last MTM snapshot
GET  /api/v1/positions/{id}                   detailed view of one position
GET  /api/v1/positions/{id}/mtm-history       mtm series for charting
GET  /api/v1/positions/{id}/alerts            exit alerts log
GET  /api/v1/positions/{id}/hedges            hedge orders log
GET  /api/v1/positions/{id}/signal-tracking   signal vs entry trail
POST /api/v1/positions/{id}/close             partial/full close (live, forwards to exec-engine)
POST /api/v1/positions/{id}/close-manual      mark for manual close (mock — Step 5 phase 1)
POST /api/v1/positions/monitor/run-once       trigger 1 cycle on demand (dev/debug)
GET  /api/v1/positions/aggregate              greeks aggregate across open positions
GET  /api/v1/positions/exit-rules-config      hot-reload config visibility
GET  /api/v1/positions/delta-hedge-config     hot-reload config visibility
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis_client_or_none
from api.orchestration.position_monitor import build_position_monitor_scheduler
from core.products import product_label_from_symbol
from persistence.models import (
    AppConfigScalar,  # replaces DeltaHedgeConfig (migration 024)
    BookedPosition,
    BookedPositionMetricHistory,
    ExitAlert,
    ExitRulesConfig,
    HedgeOrder,
    OpenPosition,
    OpenPositionHistory,
    StructureOrder,
    TradeEvent,
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
    """Closest FX OTC tenor pillar (1W / 2W / 1M / 2M / 3M / 6M / 1Y / 2Y).
    Returns None if maturity is unset."""
    if maturity is None:
        return None
    today = datetime.now(UTC).date()
    try:
        days = (maturity - today).days
    except TypeError:
        return None
    if days < 0:
        return "expired"
    if days <= 10:
        return "1W"
    if days <= 21:
        return "2W"
    if days <= 45:
        return "1M"
    if days <= 75:
        return "2M"
    if days <= 105:
        return "3M"
    if days <= 165:
        return "6M"
    if days <= 270:
        return "9M"
    if days <= 460:
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
        "structure": pos.structure,
        "structure_type": structure_label,
        "product_label": pos.product_label,
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
        "last_mtm_at": pos.timestamp.isoformat() if pos.timestamp else None,
        "ib_reconciled_at": pos.timestamp.isoformat() if pos.timestamp else None,
        "ib_qty_total": round(signed_qty) if pos.quantity is not None else None,
        "ib_qty_diff": 0,
        "ib_sync_status": _ib_sync_status(pos.timestamp),
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
        "structure": None,
        "structure_type": struct.structure_type if struct else None,
        "product_label": struct.product_label if struct else None,
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


@router.get("/open")
async def list_open(db: DbDep) -> list[dict[str, Any]]:
    """Raw ``open_position`` rows — one record per live IB contract.

    Direct mirror of the table : risk-engine UPDATEs greeks / market_price /
    pnl every 2 s, position_sync_loop INSERTs / DELETEs rows every 30 s on
    IB diffs. No join, no merge — the panel renders exactly what the DB
    holds. ``open_position_history`` carries the time series (snapshot
    per cycle) with the same shape minus the FK / current-state state.
    """
    rows = (await db.execute(
        select(OpenPosition).order_by(desc(OpenPosition.entry_timestamp))
    )).scalars().all()

    def _f(v):  # Decimal → float for JSON
        return float(v) if v is not None else None

    # Column order : identity / grouping → spec → P&L → main greeks →
    # secondary greeks → metadata. Python dicts preserve insertion order
    # (>= 3.7) so JSON consumers see the same order.
    return [{
        # ── Identity & grouping (Murex-style id stack) ──
        "id": r.id,
        "package_id": r.package_id,
        "trade_id": r.trade_id,
        "contract_id": r.contract_id,
        "product_label": r.product_label,
        "structure": r.structure,
        "side": r.side,
        # ── Spec ──
        "quantity": _f(r.quantity),
        "tenor": r.tenor,
        "expiry": r.expiry.isoformat() if r.expiry else None,
        # ── P&L & pricing ──
        "current_pnl_usd": _f(r.current_pnl_usd),
        "market_price": _f(r.market_price),
        "contract_price_entry": _f(r.contract_price_entry),
        "nominal_eur": _f(r.nominal_eur),
        # ── Main greeks ──
        "delta_usd": _f(r.delta_usd),
        "gamma_usd": _f(r.gamma_usd),
        "vega_usd": _f(r.vega_usd),
        "theta_usd": _f(r.theta_usd),
        "iv": _f(r.iv),
        # ── Secondary greeks ──
        "vanna_usd": _f(r.vanna_usd),
        "volga_usd": _f(r.volga_usd),
        # ── Metadata ──
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "entry_timestamp": r.entry_timestamp.isoformat() if r.entry_timestamp else None,
    } for r in rows]


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
        # Skip futures — the IB-live ``position`` row carries the real qty,
        # market_price, P&L and delta_usd ; the booked_position is empty by
        # design (no premium, no greeks for a linear payoff) so showing it
        # twice clutters Panel E.
        if struct and struct.structure_type in ("future_buy", "future_sell"):
            continue
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
    # Theme 4 migration 024 : delta_hedge_config folded into app_config_scalar
    # with namespace='delta_hedge'. Response shape preserved for API stability.
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
    """Signal drift history. Reads from BookedPositionMetricHistory rows where
    triggering_pc is set (signal-driven positions only). Folded into mtm rows
    in migration 026 (Theme 2)."""
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


class ClosePositionRequest(BaseModel):
    qty: int = Field(gt=0, description="Number of contracts to close. Must be ≤ open qty.")
    # Optional explicit LimitOrder override. Default behaviour :
    #   - during RTH ⇒ MarketOrder (fills instantly at touch)
    #   - outside RTH ⇒ LimitOrder at ``market_price × (1 ± OUTSIDE_RTH_SLIPPAGE)``
    #     on the close side (100 bps default ; guaranteed marketable at next open)
    # The operator only sets this field when they want a specific
    # price (e.g. cleanup of a stuck order).
    limit_price: float | None = Field(default=None, gt=0)


# Slippage applied to the close LimitOrder when we fall back outside
# RTH. 5 bps = 0.05% — marketable on liquid CME FX contracts without
# meaningfully impacting close P&L (typical option spread is wider).
# Used only on the LMT fallback ; the RTH path stays MKT, no slippage.
_OUTSIDE_RTH_SLIPPAGE = 0.0005

# CME Globex regular trading hours for EUR FX futures + options on EUR.
# Continuous session : Sunday 17:00 CT → Friday 16:00 CT, with a daily
# break 16:00 → 17:00 CT (sessional reset).  IB Gateway rejects ``MarketOrder``s
# on FOP outside this window — we detect it client-side and switch to LMT
# so the operator's intent goes through either way.
_CT_TZ = ZoneInfo("America/Chicago")


def _is_cme_fx_rth(now_utc: datetime | None = None) -> bool:
    if now_utc is None:
        now_utc = datetime.now(UTC)
    ct = now_utc.astimezone(_CT_TZ)
    wd = ct.weekday()  # Monday=0 .. Sunday=6
    h = ct.hour
    if wd == 5:                      # Saturday closed all day
        return False
    if wd == 6:                      # Sunday : reopens at 17:00 CT
        return h >= 17
    if wd == 4 and h >= 16:          # Friday : closes at 16:00 CT
        return False
    if h == 16:                      # Daily break 16:00–17:00 CT (Mon–Thu)
        return False
    return True


def _close_limit_from_mark(mark: float, side: str) -> float:
    """Compute a marketable LimitOrder price on the reverse direction.

    Closing a BUY (long) means SELLing → price slightly below mark.
    Closing a SELL (short) means BUYing → price slightly above mark.
    """
    if side == "BUY":
        return mark * (1.0 - _OUTSIDE_RTH_SLIPPAGE)
    return mark * (1.0 + _OUTSIDE_RTH_SLIPPAGE)


def _contract_fields_from_symbol(local_symbol: str, side: str) -> dict[str, Any]:
    """Map an IB ``localSymbol`` + the position side onto the
    ``trade_order`` contract columns (``contract_symbol``,
    ``contract_type``, ``contract_strike``, ``contract_expiry`` is
    handled separately because the DB stores it as Date).
    """
    spec = parse_local_symbol(local_symbol)
    if spec is None:
        # Defensive default — log row will be incomplete but legal.
        return {
            "contract_symbol": "EUR", "contract_type": "future",
            "contract_strike": None,
        }
    if spec.instrument_type == "OPTION":
        return {
            "contract_symbol": "EUR",  # all CME EUR FOPs
            "contract_type": (spec.option_type or "").lower(),  # "call" / "put"
            "contract_strike": float(spec.strike) if spec.strike is not None else None,
        }
    # FUTURE — symbol "EUR" for full-size 6E, "M6E" for micro.
    return {
        "contract_symbol": spec.symbol,
        "contract_type": "future",
        "contract_strike": None,
    }


def _structure_type_for_close(local_symbol: str, side: str) -> str:
    """Pick a ``structure_type`` for the closing TradeStructure row.

    Symbol-derived, kept minimal :
      - vanilla call / put when the IB symbol is an option
      - future_buy / future_sell when it's a future (side = OUR position
        side, since closing means reversing it)
    A future enhancement could resolve the parent live structure_type
    (e.g. straddle_atm) so the row reads "Straddle" instead — out of
    scope here ; ``product_label`` is consistent either way thanks to
    ``core.products.product_label_from_symbol``.
    """
    spec = parse_local_symbol(local_symbol)
    if spec is None:
        return "future_buy"  # last-resort default
    if spec.instrument_type == "OPTION":
        return "vanilla_call" if spec.option_type == "CALL" else "vanilla_put"
    return "future_buy" if side == "BUY" else "future_sell"


async def close_one_open_position(
    *,
    db: AsyncSession,
    pos: OpenPosition,
    qty: int,
    limit_price_override: float | None = None,
) -> dict[str, Any]:
    """Close ``qty`` contracts of a single ``open_position`` row.

    Public helper extracted from ``close_live_position`` so other
    endpoints (notably the trade-level close that closes every leg of
    a trade_structure together) can reuse the exact same audit + IB
    submission flow without duplicating the logic.

    Raises :
        - ``HTTPException(400)`` on validation errors (zero qty, qty
          exceeds open, no market price outside RTH, …).
        - ``HTTPException(503)`` on execution-engine unreachable.
        - ``HTTPException(r.status_code)`` on exec-engine refusal.

    The caller commits / rollbacks the session.
    """
    open_qty = int(abs(pos.quantity))
    if open_qty == 0:
        raise HTTPException(400, f"position #{pos.id} has zero open qty")
    if qty > open_qty:
        raise HTTPException(
            400, f"qty {qty} exceeds open qty {open_qty} on position #{pos.id}",
        )
    # Order type selection :
    #   1. Operator-supplied limit_price wins — always LMT at that price.
    #   2. Else inside RTH → MarketOrder (instant fill at touch).
    #   3. Else outside RTH → LMT at mark × (1 ± 5 bps) on close side.
    #      Reject if no mark available (caller must then supply limit_price).
    reverse_side = "SELL" if pos.side == "BUY" else "BUY"
    if limit_price_override is not None:
        limit_price: float | None = round(float(limit_price_override), 6)
    elif _is_cme_fx_rth():
        limit_price = None  # → MKT
    else:
        if pos.market_price is None:
            raise HTTPException(
                400,
                f"position #{pos.id} has no market_price and CME is outside RTH ; "
                "supply an explicit limit_price",
            )
        limit_price = round(
            _close_limit_from_mark(float(pos.market_price), pos.side), 6,
        )
    order_type = "LMT" if limit_price is not None else "MKT"

    # ── 1. Persist trade_structure + trade_order rows (audit + UI). ──
    structure_type = _structure_type_for_close(pos.structure, pos.side)
    product_label  = product_label_from_symbol(pos.structure, structure_type)
    closing_struct = TradeStructure(
        structure_type=structure_type,
        product_label=product_label,
        reference_tenor=pos.tenor or "1M",
        expiry_date=pos.expiry,
        base_qty=qty,
        state="submitted",
        execution_mode="live",
    )
    db.add(closing_struct)
    await db.flush()  # populates closing_struct.id

    contract_fields = _contract_fields_from_symbol(pos.structure, pos.side)
    closing_order = StructureOrder(
        structure_id=closing_struct.id,
        leg_idx=0, order_role="closing",
        side=reverse_side, qty=qty,
        order_type=order_type, limit_price=limit_price,
        contract_expiry=pos.expiry,
        state="pending",
        **contract_fields,
    )
    db.add(closing_order)

    # NOTE: TradeEvent.position_id FKs to booked_position.id, not the
    # IB-live ``position`` table — leave it NULL and store the live
    # position id in the payload for traceability.
    db.add(TradeEvent(
        structure_id=closing_struct.id,
        event_type="position_close_initiated",
        severity="info",
        description=(
            f"manual close live#{pos.id} : {qty}/{open_qty} {order_type}"
            + (f" @ {limit_price}" if limit_price is not None else "")
        ),
        payload={
            "live_position_id": pos.id,
            "local_symbol": pos.structure,
            "qty": qty,
            "open_qty_before": open_qty,
            "order_type": order_type,
            "limit_price": limit_price,
            "reverse_side": reverse_side,
        },
    ))
    await db.commit()

    # ── 2. Forward to execution-engine. ──
    payload: dict[str, Any] = {
        "local_symbol": pos.structure,
        "qty": qty,
        # Plumb the DB ``trade_order.id`` so the exec-engine can attach
        # fills_handler callbacks ; without this, qty_filled stays at 0
        # on the closing row even after IB fills the order.
        "db_order_id": closing_order.id,
    }
    if limit_price is not None:
        payload["limit_price"] = limit_price
    exec_url = os.getenv("EXECUTION_URL", "http://execution-engine:8001")
    url = f"{exec_url}/internal/positions/close-by-symbol"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
    except httpx.HTTPError as e:
        # Mark the closing structure as failed so the operator sees it.
        closing_struct.state = "partial_fail"
        closing_order.state = "rejected"
        closing_order.rejection_text = f"execution-engine unreachable: {e}"[:300]
        await db.commit()
        raise HTTPException(503, f"execution-engine unreachable: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        closing_struct.state = "partial_fail"
        closing_order.state = "rejected"
        closing_order.rejection_text = str(detail)[:300]
        await db.commit()
        raise HTTPException(r.status_code, detail)

    ib_response = r.json()

    # ── 3. Stamp IB orderId + flip the leg state to 'submitted'. ──
    ib_order_id = ib_response.get("order_id")
    if ib_order_id is not None:
        closing_order.ib_order_id = str(ib_order_id)
    perm_id = ib_response.get("perm_id")
    if perm_id is not None:
        closing_order.ib_perm_id = str(perm_id)
    closing_order.state = "submitted"
    closing_order.submitted_at = datetime.now(UTC)
    await db.commit()

    return {
        "position_id": pos.id,
        "local_symbol": pos.structure,
        "closed_qty": qty,
        "open_qty_before": open_qty,
        "order_type": order_type,
        "limit_price": limit_price,
        "structure_id": closing_struct.id,
        "order_id": closing_order.id,
        "ib": ib_response,
    }


@router.post("/{position_id}/close")
async def close_live_position(
    position_id: int, body: ClosePositionRequest, db: DbDep,
) -> dict[str, Any]:
    """Partial / full close on an IB-live ``open_position`` row.

    Thin wrapper over :func:`close_one_open_position`. The trade-level
    close endpoint (``POST /api/v1/trades/{trade_id}/close``) calls the
    same helper once per leg.
    """
    pos = (await db.execute(
        select(OpenPosition).where(OpenPosition.id == position_id).limit(1)
    )).scalar_one_or_none()
    if pos is None:
        raise HTTPException(404, f"position #{position_id} not found")
    return await close_one_open_position(
        db=db, pos=pos, qty=body.qty, limit_price_override=body.limit_price,
    )


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
