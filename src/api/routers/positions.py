"""Step 5 — Active positions monitoring API.

GET  /api/v1/positions/active                 list open positions + last MTM snapshot
GET  /api/v1/positions/{id}                   detailed view of one position
GET  /api/v1/positions/{id}/mtm-history       mtm series for charting
GET  /api/v1/positions/{id}/alerts            exit alerts log
GET  /api/v1/positions/{id}/hedges            hedge orders log
GET  /api/v1/positions/{id}/signal-tracking   signal vs entry trail
GET  /api/v1/positions/reconciliation         book (filled orders) vs broker (IB mirror) breaks
GET  /api/v1/positions/ledger                 positions + P&L folded from the trade_fill event log
POST /api/v1/positions/{id}/close             partial/full close (live, forwards to exec-engine)
POST /api/v1/positions/{id}/close-manual      mark for manual close (mock — Step 5 phase 1)
POST /api/v1/positions/monitor/run-once       trigger 1 cycle on demand (dev/debug)
GET  /api/v1/positions/aggregate              greeks aggregate across open positions
GET  /api/v1/positions/exit-rules-config      hot-reload config visibility
GET  /api/v1/positions/delta-hedge-config     hot-reload config visibility
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_write
from api.config import get_settings
from api.dependencies import get_db_session, get_redis_client_or_none
from api.orchestration.position_lines import line_fields, load_line_set, mirror_rows
from api.orchestration.position_lines import summary as line_summary
from api.orchestration.position_monitor import build_position_monitor_scheduler
from api.schemas import route_models as rm
from api.schemas.reads import DeltaHedgeSetting, ExitRuleConfig, OpenPositionRow
from bus import keys
from core.contracts import multiplier_for, parse_local_symbol
from core.execution.reconciliation import classify_break
from core.ledger import LedgerFill, fold_fills, unrealized_pnl
from core.products import product_label_from_symbol
from core.vol.tenors import tenor_bucket_from_expiry
from persistence.models import (
    AppConfigScalar,  # replaces DeltaHedgeConfig (migration 024)
    BookedPosition,
    BookedPositionMetricHistory,
    ExitAlert,
    ExitRulesConfig,
    HedgeOrder,
    LegPosition,
    OpenPosition,
    OpenPositionHistory,
    ReconciliationBreak,
    StructureFill,
    StructureOrder,
    TradeEvent,
    TradeStructure,
)
from persistence.order_state import end_order, mark_submitted
from persistence.reservation import recompute_reservation
from shared.internal_auth import internal_headers
from shared.trace import current_trace_id

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

async def _read_contract_marks(redis: Any) -> dict[int, float]:
    """Read the Redis hash ``contract_marks:EUR`` populated by execution-engine.
    Returns ``{position_id: marketPrice}`` for ALL contract types — the
    futures price for FUT rows, the option premium per unit for OPTIONs.
    """
    if redis is None:
        return {}
    try:
        raw = await redis.hgetall(keys.CONTRACT_MARKS.format(symbol="EUR"))
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


@router.get("/open", response_model=list[OpenPositionRow])
async def list_open(db: DbDep) -> list[dict[str, Any]]:
    """One row per position LINE, with the broker mirror's columns.

    The rows are the shared line set (``core.positions.line_set``, #461), the
    same lines as ``/positions/structured`` and ``/portfolio/pnl-attribution``.
    A line the broker holds carries the ``open_position`` numbers (risk-engine
    greeks, mark, P&L; scaled to the line's share when several trades hold the
    contract). A line only the book holds is listed too, flagged
    ``line_status = book_only``, with unknown numbers as null.
    """
    ls = await load_line_set(db)
    rows = mirror_rows(ls, datetime.now(UTC).date())
    rows.sort(key=lambda r: r.get("entry_timestamp") or "", reverse=True)
    return rows


@router.get("/lines")
async def list_lines(db: DbDep) -> dict[str, Any]:
    """The position line set itself: ids and statuses, no market columns.
    What the three panels must agree on; used by the parity checks."""
    ls = await load_line_set(db)
    out = line_summary(ls)
    out["lines"] = [
        {**line_fields(ln, ls), "contract": ln.contract, "trade_id": ln.trade_id, "order_ids": list(ln.order_ids)}
        for ln in ls.lines
    ]
    return out


@router.get("/book", response_model=list[rm.PositionsBookRow], response_model_exclude_unset=True)
async def list_book(db: DbDep) -> list[dict[str, Any]]:
    """The BOOK — one row per leg, position folded forward from that leg's fills.

    This is the authority for "what we hold" (invariants I3/I7): ``open_qty`` is a
    pure signed fold of the leg's ``trade_fill`` rows (via ``leg_position``, the
    ``position_projector`` output), never back-attributed from the netted IB mirror
    (``open_position``, which /open exposes and which stays a reconciliation
    checksum only). ``available = |open_qty| − reserved_qty`` is the close-guard
    headroom (I5). Additive read; the frontend is untouched.
    """
    rows = (await db.execute(
        select(LegPosition, StructureOrder, TradeStructure)
        .join(StructureOrder, LegPosition.order_id == StructureOrder.id)
        .join(TradeStructure, StructureOrder.structure_id == TradeStructure.id, isouter=True)
        .order_by(desc(LegPosition.rebuilt_at))
    )).all()

    def _f(v):  # Decimal → float for JSON
        return float(v) if v is not None else None

    out: list[dict[str, Any]] = []
    for lp, order, struct in rows:
        open_qty = float(lp.open_qty or 0)
        reserved = float(lp.reserved_qty or 0)
        out.append({
            "order_id": order.id,
            "structure_id": order.structure_id,
            "structure_type": struct.structure_type if struct else None,
            "leg_idx": order.leg_idx,
            "order_role": order.order_role,
            "side": order.side,
            "contract_type": order.contract_type,
            "contract_strike": _f(order.contract_strike),
            "contract_expiry": order.contract_expiry.isoformat() if order.contract_expiry else None,
            "ib_local_symbol": order.ib_local_symbol,
            "open_qty": open_qty,
            "reserved_qty": reserved,
            "available": abs(open_qty) - reserved,
            "avg_price": _f(lp.avg_price),
            "rebuilt_at": lp.rebuilt_at.isoformat() if lp.rebuilt_at else None,
        })
    return out


@router.get("/breaks", response_model=list[rm.PositionsBreaksRow], response_model_exclude_unset=True)
async def list_breaks(db: DbDep, include_resolved: bool = Query(False)) -> list[dict[str, Any]]:
    """Materialised reconciliation breaks (I4) — book (leg_position) vs broker
    (IB mirror) gaps. Open breaks (``resolved_at`` NULL) by default; a break is
    data that lives and resolves, never a silent discrepancy. Written by the
    execution-engine ``reconcile_positions_loop``. Additive read."""
    stmt = select(ReconciliationBreak)
    if not include_resolved:
        stmt = stmt.where(ReconciliationBreak.resolved_at.is_(None))
    rows = (await db.execute(stmt.order_by(desc(ReconciliationBreak.detected_at)))).scalars().all()

    def _f(v):
        return float(v) if v is not None else None

    return [{
        "id": r.id,
        "contract": r.local_symbol,
        "book_qty": _f(r.book_qty),
        "broker_qty": _f(r.broker_qty),
        "diff": _f(r.diff),
        "break_type": r.break_type,
        "detected_at": r.detected_at.isoformat() if r.detected_at else None,
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    } for r in rows]


@router.get("/structured", response_model=rm.PositionsStructured, response_model_exclude_unset=True)
async def list_structured(db: DbDep, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Position lines grouped by the booked ``trade_structure``: a Risk
    Reversal reads as ONE 2-leg group labelled from ``structure_type`` +
    ``reference_tenor`` (the values you actually traded).

    The rows are the shared line set (``core.positions.line_set``, #461): the
    same lines as ``/positions/open`` and ``/portfolio/pnl-attribution``. A
    trade is listed when it owns at least one line; a leg is ``open`` when it
    carries a line. Lines no trade explains come back as ``unlinked``.

    ``limit`` is accepted for compatibility and ignored: what is visible
    follows what is held, not how recently the trade was created. The old
    rule (the 50 newest trades owning a mirror row, then every leg the book
    said was open) showed legs the broker no longer held and hid old trades
    still held.
    """
    del limit

    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    ls = await load_line_set(db)
    today = datetime.now(UTC).date()
    structs = sorted(ls.structures.values(), key=lambda s: (s.created_at is None, s.created_at), reverse=True)
    legs_by_struct: dict[int, list[StructureOrder]] = {}
    if structs:
        for lg in (await db.execute(
            select(StructureOrder).where(StructureOrder.structure_id.in_([s.id for s in structs]))
            .order_by(StructureOrder.structure_id, StructureOrder.leg_idx)
        )).scalars().all():
            legs_by_struct.setdefault(int(lg.structure_id), []).append(lg)
    book_by_order: dict[int, LegPosition] = {}
    order_ids = [int(lg.id) for lgs in legs_by_struct.values() for lg in lgs]
    if order_ids:
        book_by_order = {
            int(bp.order_id): bp for bp in (await db.execute(
                select(LegPosition).where(LegPosition.order_id.in_(order_ids))
            )).scalars().all()
        }

    def _scaled(v: Any, share: float) -> float | None:
        return float(v) * abs(share) if v is not None else None

    def _marks(p: OpenPosition | None, share: float = 1.0) -> dict[str, Any]:
        # Additive columns carry the line's share of the holding; a price, an
        # IV are per unit and are not scaled. No holding: unknown, not zero.
        return {
            "mark": _f(p.market_price) if p else None,
            "pnl_usd": _scaled(p.current_pnl_usd, share) if p else None,
            "delta_usd": _scaled(p.delta_usd, share) if p else None,
            "gamma_usd": _scaled(p.gamma_usd, share) if p else None,
            "vega_usd": _scaled(p.vega_usd, share) if p else None,
            "theta_usd": _scaled(p.theta_usd, share) if p else None,
            "vanna_usd": _scaled(p.vanna_usd, share) if p else None,
            "volga_usd": _scaled(p.volga_usd, share) if p else None,
            "iv": _f(p.iv) if p else None,
        }

    def _leg_dict(lg: StructureOrder) -> dict[str, Any]:
        # One hydrated leg : DB identity/terms (from the StructureOrder) + the
        # line it carries + the live IB mirror behind that line.
        ln = ls.for_order(int(lg.id))
        lp = ls.positions.get(ln.position_id) if ln is not None and ln.position_id is not None else None
        share = ln.share if ln is not None else 1.0
        bp = book_by_order.get(int(lg.id))
        return {
            "leg_idx": lg.leg_idx, "contract_type": lg.contract_type, "side": lg.side, "qty": lg.qty,
            "strike": _f(lg.contract_strike),
            # The contract's own expiry when the broker holds it; the booked
            # date (today + tenor at preview time) is only an entry detail.
            "expiry": (
                lp.expiry.isoformat() if lp is not None and lp.expiry
                else (lg.contract_expiry.isoformat() if lg.contract_expiry else None)
            ),
            "booked_expiry": lg.contract_expiry.isoformat() if lg.contract_expiry else None,
            "order_role": lg.order_role,
            "state": lg.state, "qty_filled": lg.qty_filled, "ib_local_symbol": lg.ib_local_symbol,
            # linked = the broker holds (part of) this leg's line.
            "linked": lp is not None,
            # open = the leg carries a position line. open_qty is the signed book holding.
            "open": ln is not None,
            "open_qty": float(bp.open_qty) if bp is not None and bp.open_qty is not None else None,
            **(line_fields(ln, ls) if ln is not None else {
                "line_id": None, "line_status": None, "book_qty": None, "broker_qty": None, "position_id": None,
                "identity": None,
            }),
            "con_id": lp.contract_id if lp else (int(lg.con_id) if lg.con_id is not None else None),
            "held_qty": abs(ln.broker_qty) if (ln is not None and lp is not None) else None,
            "held_side": lp.side if lp else None,
            # entry price: the mirror's when linked, else the book's avg fill price.
            "entry": _f(lp.contract_price_entry) if lp else (_f(bp.avg_price) if bp is not None else None),
            "nominal_eur": _scaled(lp.nominal_eur, share) if lp else None,
            # The bucket by days to expiry, the same rule as /positions/open
            # (tenor_now) and the attribution, so one line reads the same
            # tenor on the three panels.
            "tenor": (tenor_bucket_from_expiry(lp.expiry, today) or lp.tenor) if lp else None,
            "opened": lp.entry_timestamp.isoformat() if (lp and lp.entry_timestamp) else None,
            "updated": lp.timestamp.isoformat() if (lp and lp.timestamp) else None,
            **_marks(lp, share),
        }

    def _net(struct_id: int) -> dict[str, float]:
        n = {"delta_usd": 0.0, "gamma_usd": 0.0, "vega_usd": 0.0, "theta_usd": 0.0, "pnl_usd": 0.0, "n_linked": 0}
        for ln in ls.for_trade(struct_id):
            p = ls.positions.get(ln.position_id) if ln.position_id is not None else None
            if p is None:
                continue
            m = _marks(p, ln.share)
            for k in ("delta_usd", "gamma_usd", "vega_usd", "theta_usd", "pnl_usd"):
                n[k] += float(m[k] or 0)
            n["n_linked"] += 1
        n["n_lines"] = len(ls.for_trade(struct_id))
        return n

    unlinked: list[dict[str, Any]] = []
    for ln in ls.lines:
        if ln.trade_id is not None:
            continue
        p = ls.positions.get(ln.position_id) if ln.position_id is not None else None
        if p is None:
            continue
        unlinked.append({
            "id": p.id, "symbol": p.structure, "product_label": p.product_label,
            "side": "BUY" if ln.broker_qty > 0 else "SELL", "qty": abs(ln.broker_qty), "tenor": p.tenor,
            "expiry": p.expiry.isoformat() if p.expiry else None,
            "entry": _f(p.contract_price_entry),
            **line_fields(ln, ls), **_marks(p, ln.share),
        })

    out_structs = [{
        "structure_id": s.id, "structure_type": s.structure_type, "product_label": s.product_label,
        "tenor": s.reference_tenor, "state": s.state, "base_qty": s.base_qty,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "legs": [_leg_dict(lg) for lg in legs_by_struct.get(int(s.id), [])],
        "net": _net(int(s.id)),
    } for s in structs]
    return {"structures": out_structs, "unlinked": unlinked}


@router.get("/reconciliation", response_model=rm.PositionsReconciliation, response_model_exclude_unset=True)
async def reconciliation(db: DbDep) -> dict[str, Any]:
    """Book vs broker reconciliation — the desk's own record vs what IB holds.

    The book (``trade_order`` filled qty, entries + closes netting out) is the
    system of record for what we *should* hold ; the IB mirror (``open_position``)
    is what the broker says we *do* hold. This surfaces the **breaks** between
    them (§2.6 of docs/BACKEND_ARCHITECTURE.md) instead of silently trusting
    either side. Read-only diagnostic.

    Reconciliation is done **per contract** (IB ``localSymbol``) because IB nets
    by contract ; a break is then attributed to a structure for display.
    """
    # 1. Expected net per contract, from FILLED orders. Entries add in their
    #    direction, closes (opposite side) subtract — so the net is what the book
    #    thinks is live. qty_filled reflects real executions even if the order row
    #    was later cancelled, so we don't gate on order state here.
    orders = (await db.execute(
        select(StructureOrder).where(StructureOrder.ib_local_symbol.is_not(None))
    )).scalars().all()
    expected: dict[str, float] = {}
    struct_by_symbol: dict[str, int | None] = {}
    for o in orders:
        sym = o.ib_local_symbol
        if sym is None:
            continue
        qf = float(o.qty_filled or 0)
        if qf == 0:
            continue
        expected[sym] = expected.get(sym, 0.0) + (qf if o.side == "BUY" else -qf)
        if o.order_role != "closing":  # attribute the contract to its ENTRY structure
            struct_by_symbol.setdefault(sym, o.structure_id)

    # 2. Actual net per contract, from the IB mirror.
    positions = (await db.execute(select(OpenPosition))).scalars().all()
    actual: dict[str, float] = {}
    for p in positions:
        signed = float(p.quantity) if p.side == "BUY" else -float(p.quantity)
        actual[p.structure] = actual.get(p.structure, 0.0) + signed
        struct_by_symbol.setdefault(p.structure, p.trade_id)

    breaks = _compute_breaks(expected, actual, struct_by_symbol)
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "n_contracts": len(set(expected) | set(actual)),
        "n_breaks": len(breaks),
        "breaks": breaks,
    }


@router.get("/ledger", response_model=rm.PositionsLedger, response_model_exclude_unset=True)
async def ledger(db: DbDep) -> dict[str, Any]:
    """Positions + realised / unrealised P&L folded from the append-only
    ``trade_fill`` event log (average-cost — see ``core.ledger``).

    Audit-grade and **reproducible from events**, independent of the mutable IB
    mirror (§2.4 of docs/BACKEND_ARCHITECTURE.md). Its net qty per contract is
    what ``/reconciliation`` calls ``expected`` — this endpoint adds the money.
    Fills are folded in **execution order** (by fill timestamp).
    """
    rows = (await db.execute(
        select(StructureFill, StructureOrder)
        .join(StructureOrder, StructureFill.order_id == StructureOrder.id)
        .order_by(StructureFill.timestamp, StructureFill.id)
    )).all()
    fills: list[LedgerFill] = []
    for fill, order in rows:
        sym = order.ib_local_symbol
        if not sym:  # can't attribute a fill with no contract symbol
            continue
        fills.append(LedgerFill(
            contract=sym, side=fill.side, qty=float(fill.qty_filled),
            price=float(fill.fill_price), commission=float(fill.commission_usd or 0),
            multiplier=multiplier_for(order.contract_symbol),
        ))
    book = fold_fills(fills)

    # Current marks from the IB mirror, only for mark-to-market of the open qty.
    marks = {
        p.structure: (float(p.market_price) if p.market_price is not None else None)
        for p in (await db.execute(select(OpenPosition))).scalars().all()
    }

    positions: list[dict[str, Any]] = []
    tot_real = tot_unreal = tot_comm = 0.0
    for sym, led in sorted(book.items()):
        if led.net_qty == 0 and led.realized_pnl == 0:
            continue  # never held, nothing realised → nothing to show
        u = unrealized_pnl(led, marks.get(sym))
        tot_real += led.realized_pnl
        tot_comm += led.commission
        tot_unreal += u or 0.0
        positions.append({
            "contract": sym,
            "net_qty": round(led.net_qty, 4),
            "avg_cost": round(led.avg_cost, 6),
            "realized_pnl": round(led.realized_pnl, 2),
            "unrealized_pnl": None if u is None else round(u, 2),
            "commission": round(led.commission, 2),
            "multiplier": led.multiplier,
        })
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "positions": positions,
        "totals": {
            "realized_pnl": round(tot_real, 2),
            "unrealized_pnl": round(tot_unreal, 2),
            "commission": round(tot_comm, 2),
        },
    }


@router.get("/active", response_model=list[rm.PositionsActiveRow], response_model_exclude_unset=True)
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


@router.get("/aggregate", response_model=rm.PositionsAggregate, response_model_exclude_unset=True)
async def aggregate_greeks(db: DbDep) -> dict[str, Any]:
    """Sum of current greeks across all open positions (for Panel 4 zone B)."""
    rows = (await db.execute(
        select(BookedPosition).where(BookedPosition.state == "open")
    )).scalars().all()
    total_vega = total_gamma = total_theta = total_delta = 0.0
    n = 0
    n_delta_unknown = 0  # positions whose latest live delta could not be computed (#310)
    for pos in rows:
        latest = (await db.execute(
            select(BookedPositionMetricHistory).where(BookedPositionMetricHistory.position_id == pos.id)
            .order_by(desc(BookedPositionMetricHistory.timestamp)).limit(1)
        )).scalar_one_or_none()
        if latest:
            total_vega += latest.current_vega_usd_per_volpt or 0.0
            total_gamma += latest.current_gamma_usd_per_pip2 or 0.0
            total_theta += latest.current_theta_usd_per_day or 0.0
            if latest.current_delta_unhedged is None:
                n_delta_unknown += 1
            else:
                total_delta += latest.current_delta_unhedged
        else:
            total_vega += pos.entry_vega_usd_per_volpt or 0.0
            total_gamma += pos.entry_gamma_usd_per_pip2 or 0.0
            total_theta += pos.entry_theta_usd_per_day or 0.0
            n_delta_unknown += 1
        n += 1
    return {
        "n_open_positions": n,
        "total_vega_usd_per_volpt": round(total_vega, 2),
        "total_gamma_usd_per_pip2": round(total_gamma, 4),
        "total_theta_usd_per_day": round(total_theta, 2),
        # Partial sum when n_delta_unknown > 0: the UI must say so, not show 0.
        "total_delta_unhedged": round(total_delta, 4),
        "n_delta_unknown": n_delta_unknown,
    }


@router.get("/exit-rules-config", response_model=list[ExitRuleConfig])
async def list_exit_rules_config(db: DbDep) -> list[dict[str, Any]]:
    """Exit rules the position monitor evaluates, highest priority first, with their parameters and whether each is active."""
    rows = (await db.execute(
        select(ExitRulesConfig).order_by(desc(ExitRulesConfig.priority))
    )).scalars().all()
    return [
        {"rule_name": r.rule_name, "is_active": r.is_active, "priority": r.priority,
         "params": r.params, "description": r.description}
        for r in rows
    ]


@router.get("/delta-hedge-config", response_model=list[DeltaHedgeSetting])
async def list_delta_hedge_config(db: DbDep) -> list[dict[str, Any]]:
    """Delta-hedge settings (thresholds, sizes, cooldown) as name / value / unit rows."""
    # delta_hedge_config rows folded into config_scalar with
    # namespace='delta_hedge' (migration 024, table renamed 037).
    # Response shape preserved for API stability.
    rows = (await db.execute(
        select(AppConfigScalar).where(AppConfigScalar.namespace == "delta_hedge")
    )).scalars().all()
    return [
        {"config_name": r.name, "config_value": r.value,
         "unit": r.unit, "description": r.description}
        for r in rows
    ]


@router.get("/{position_id}", response_model=rm.PositionDetail, response_model_exclude_unset=True)
async def get_position(position_id: int, db: DbDep) -> dict[str, Any]:
    """One booked position with its structure and its latest metrics row (greeks, mark, P&L). 404 when the id is unknown."""
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


@router.get("/{position_id}/mtm-history", response_model=list[rm.PositionMtmPoint], response_model_exclude_unset=True)
async def mtm_history(
    position_id: int, db: DbDep, hours: int = Query(24, ge=1, le=720),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    """Mark-to-market history of one position over the last `hours`: spot, average leg IV, gross / net P&L and its vega / gamma / theta / other split."""
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


@router.get("/{position_id}/alerts", response_model=list[rm.PositionAlertRow], response_model_exclude_unset=True)
async def position_alerts(
    position_id: int, db: DbDep, limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Exit alerts raised for one position, newest first: rule, recommended action, priority and execution status."""
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


@router.get("/{position_id}/hedges", response_model=list[rm.PositionHedgeRow], response_model_exclude_unset=True)
async def position_hedges(
    position_id: int, db: DbDep, limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Delta-hedge orders triggered for one position, newest first."""
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


@router.get("/{position_id}/signal-tracking", response_model=list[rm.SignalTrackingPoint], response_model_exclude_unset=True)
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


def _compute_breaks(
    expected: dict[str, float],
    actual: dict[str, float],
    struct_by_symbol: dict[str, int | None],
) -> list[dict[str, Any]]:
    """Reconcile book (``expected`` net qty per contract, from filled orders) vs
    broker (``actual`` net qty per contract, from the IB mirror). Pure — the
    endpoint just gathers the two dicts; the diff logic is here so it's testable
    without a DB.

    ``expected`` / ``actual`` are **signed** nets keyed by IB ``localSymbol``
    (BUY = +, SELL = −). A non-zero ``expected − actual`` is a *break*, classified:
      - ``missing_at_ib``   book holds it, IB is flat (fill not reflected / recon lag)
      - ``unbooked_at_ib``  IB holds it, the book has no record (manual/orphan)
      - ``direction``       signs disagree (we think long, IB is short)
      - ``quantity``        both hold it, sizes differ
    """
    breaks: list[dict[str, Any]] = []
    for sym in sorted(set(expected) | set(actual)):
        exp = round(expected.get(sym, 0.0), 4)
        act = round(actual.get(sym, 0.0), 4)
        kind = classify_break(exp, act)  # shared with the materialising reconciler
        if kind is None:
            continue
        breaks.append({
            "contract": sym, "expected_net": exp, "actual_net": act,
            "break": round(exp - act, 4), "kind": kind,
            "structure_id": struct_by_symbol.get(sym),
        })
    return breaks


# Option (FOP) tick + how far a close CROSSES the spread off the mark.
# A plain MarketOrder on an option hits IB's option price-cap protection and only
# dribbles partial fills (never completing → the operator re-clicks → stacked
# closes), so an option close is ALWAYS a marketable LMT priced well through the
# touch. Wide because option spreads are wide ; snapped to the 0.0001 grid or IB
# rejects with Warning 110. Same buffer as the entry path (MARKETABLE_LIMIT_BUFFER).
_OPT_TICK = 0.0001
_MKT_CLOSE_BUFFER = get_settings().MARKETABLE_LIMIT_BUFFER

# How long a closing order counts as "in flight" for the stacking guard. Past
# this, an unfilled close is treated as stuck/cancelled and no longer blocks a
# fresh close (a cancelled order's DB row isn't always flipped terminal). A real
# option close fills in ~30 s, so 3 min is a generous ceiling.
_INFLIGHT_CLOSE_WINDOW = timedelta(minutes=get_settings().CLOSE_INFLIGHT_WINDOW_MIN)
# Budget for the engine to ack a close at IB. The engine no longer runs its
# position sync inside this request (#319), so an IB ack is all it waits for.
_CLOSE_DISPATCH_TIMEOUT_S = get_settings().CLOSE_DISPATCH_TIMEOUT_S

DispatchFailure = Literal["not_sent", "unknown"]


def classify_dispatch_failure(exc: httpx.HTTPError) -> DispatchFailure:
    """Did the close request reach the execution engine?

    ``not_sent``  connect-level failure (refused, DNS, connect timeout): the
                  engine never saw the request, nothing can be live at IB.
    ``unknown``   anything after the connection was made (read / write / pool
                  timeout, broken response): the engine may have placed the
                  order. EX-01: such a close must stay in flight, never be
                  stamped rejected (pure, tested).
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "not_sent"
    return "unknown"


def close_inflight_remaining(
    qty: int,
    qty_filled: int | None,
    submitted_at: datetime | None,
    state_updated_at: datetime | None,
    now: datetime,
    window: timedelta,
    state: str | None = None,
) -> int:
    """Unfilled qty of a close order that still counts against the stacking
    guard (pure/testable).

    A close counts for as long as it can still fill: there is no time window
    on an order that was dispatched (#486). The window used to make a close
    resting more than 3 minutes invisible, and a second full-size close was
    accepted on top of it (prod 2026-11-12: two SELL 100 on a long of 100).
    Such an order ends by a fill, a cancel, or the reaper, never by ageing.

    The one case that ages out is a close that was CREATED and never
    dispatched (``state == 'pending'``, e.g. the engine was down): it holds
    nothing at the broker, no loop will ever move it, and without the window
    it would block every new close for ever.
    """
    if state == "pending":
        age_ts = submitted_at if submitted_at is not None else state_updated_at
        if age_ts is not None and age_ts < now - window:
            return 0
    return max(0, int(qty) - int(qty_filled or 0))


def _marketable_close_from_mark(mark: float, pos_side: str) -> float | None:
    """Aggressive, tick-snapped LMT that crosses the spread to close.

    ``pos_side`` is OUR side: closing a long (BUY) → we SELL below the mark ;
    closing a short (SELL) → we BUY above it. Returns None if the mark is
    non-positive (caller then falls back to a plain market order).
    """
    if mark <= 0:
        return None
    if pos_side == "BUY":  # long → SELL to close, price through the bid
        lp = math.floor(mark * (1.0 - _MKT_CLOSE_BUFFER) / _OPT_TICK) * _OPT_TICK
        return round(lp, 6) if lp >= _OPT_TICK else _OPT_TICK
    # short → BUY to close, price through the ask
    lp = math.ceil(mark * (1.0 + _MKT_CLOSE_BUFFER) / _OPT_TICK) * _OPT_TICK
    return round(lp, 6)


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
    entry_order_id_override: int | None = None,
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
    spot_spec = parse_local_symbol(pos.structure)
    if spot_spec is not None and spot_spec.instrument_type == "SPOT":
        # The close flow below builds a listed-contract order (future / FOP);
        # a spot position must never be turned into one.
        raise HTTPException(
            400, f"position #{pos.id} is spot FX: close it with a spot ticket, not the close panel",
        )
    open_qty = int(abs(pos.quantity))
    if open_qty == 0:
        raise HTTPException(400, f"position #{pos.id} has zero open qty")
    if qty > open_qty:
        raise HTTPException(
            400, f"qty {qty} exceeds open qty {open_qty} on position #{pos.id}",
        )
    # Stacking guard : a close only shows filled once IB actually fills it (~30 s
    # for options), but the panel still shows the open position meanwhile — so the
    # operator re-clicks and every click used to stack ANOTHER full-size close.
    # Refuse if live closing orders on this exact contract already cover the open
    # qty (prevents over-closing / flipping the book with a pile of market orders).
    # Self-healing : only count RECENT closing orders. A close that's been
    # submitted longer than the window without filling is stuck/cancelled at IB
    # (its DB row may never get flipped terminal), so it must not block a fresh
    # close forever — otherwise a cancelled order permanently locks the position.
    spec = parse_local_symbol(pos.structure)
    is_option = spec is not None and spec.instrument_type == "OPTION"
    strike_val = float(spec.strike) if (spec and spec.strike is not None) else None
    reverse_side = "SELL" if pos.side == "BUY" else "BUY"
    now = datetime.now(UTC)
    # One close decision at a time per position: the check below and the rows
    # written after it are one unit (two simultaneous requests used to both
    # pass the check). A no-op on engines without row locks.
    await db.execute(select(OpenPosition.id).where(OpenPosition.id == pos.id).with_for_update())
    contract_kind = (spec.option_type or "").lower() if is_option else "future"
    # The guard keys on the CONTRACT (#486): the close row carries the
    # position's IB symbol from its creation. Side, expiry, strike and kind
    # only matched rows written before that (a 6E and an M6E future of the
    # same month shared one budget).
    legacy = [
        StructureOrder.ib_local_symbol.is_(None),
        StructureOrder.contract_expiry == pos.expiry,
        StructureOrder.contract_type == contract_kind,
    ]
    if strike_val is not None:
        legacy.append(StructureOrder.contract_strike == strike_val)
    inflight_conds = [
        StructureOrder.order_role == "closing",
        StructureOrder.side == reverse_side,
        StructureOrder.state.in_(("pending", "submitted", "partially_filled")),
        or_(StructureOrder.ib_local_symbol == pos.structure, and_(*legacy)),
    ]
    # Sum the still-in-flight remaining qty in Python via the pure predicate — the
    # self-heal boundary (incl. the never-dispatched submitted_at IS NULL case)
    # lives in one tested place instead of drifting inside a SQL expression.
    close_orders = (await db.execute(
        select(StructureOrder).where(*inflight_conds)
    )).scalars().all()
    already_closing = sum(
        close_inflight_remaining(
            o.qty, o.qty_filled, o.submitted_at, o.state_updated_at,
            now, _INFLIGHT_CLOSE_WINDOW, o.state,
        )
        for o in close_orders
    )
    if already_closing + qty > open_qty:
        raise HTTPException(
            409,
            f"position #{pos.id} already has {already_closing} contract(s) closing "
            f"(open {open_qty}) — refusing to stack another close order. Wait for "
            "the in-flight close to fill or cancel it first.",
        )

    # Order type selection :
    #   1. Operator-supplied limit_price wins — always LMT at that price.
    #   2. Options → ALWAYS a marketable LMT crossing the spread off the mark
    #      (in RTH too : IB's option price-cap makes a plain MKT dribble/hang,
    #      never completing). Falls back to MKT only if there's no mark to price.
    #   3. Futures inside RTH → MarketOrder (deep book, no cap).
    #   4. Futures outside RTH → LMT at mark × (1 ± 5 bps) on the close side.
    if limit_price_override is not None:
        limit_price: float | None = round(float(limit_price_override), 6)
    elif is_option and pos.market_price is not None:
        limit_price = _marketable_close_from_mark(float(pos.market_price), pos.side)
    elif _is_cme_fx_rth():
        limit_price = None  # → MKT (futures, or an option with no mark)
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
    trace_id = current_trace_id()  # correlation id of this close request
    closing_struct = TradeStructure(
        structure_type=structure_type,
        product_label=product_label,
        reference_tenor=pos.tenor or "1M",
        expiry_date=pos.expiry,
        base_qty=qty,
        state="submitted",
        execution_mode="live",
        trace_id=trace_id,
    )
    db.add(closing_struct)
    await db.flush()  # populates closing_struct.id

    contract_fields = _contract_fields_from_symbol(pos.structure, pos.side)
    # OMS P2 : best-effort link to the entry leg this close covers, so the
    # reservation ledger can materialise reserved_qty on it (I5). The stateless
    # over-close guard above stays the admission gate; this makes the reservation
    # persistent + race-visible. NULL when the entry leg can't be resolved.
    # A trade-level close passes the EXACT entry leg it targets
    # (entry_order_id_override) so a shared-contract sibling can't be mis-linked.
    # Otherwise fall back to a best-effort match by the mirror's (netted) trade_id.
    entry_order_id: int | None = entry_order_id_override
    if entry_order_id is None and pos.trade_id is not None:
        entry_order_id = (await db.execute(
            select(StructureOrder.id)
            .where(StructureOrder.structure_id == pos.trade_id)
            .where(StructureOrder.order_role != "closing")
            .where(StructureOrder.ib_local_symbol == pos.structure)
            .limit(1)
        )).scalar_one_or_none()
    closing_order = StructureOrder(
        structure_id=closing_struct.id,
        leg_idx=0, order_role="closing",
        side=reverse_side, qty=qty,
        order_type=order_type, limit_price=limit_price,
        contract_expiry=pos.expiry,
        state="pending",
        trace_id=trace_id,
        closes_order_id=entry_order_id,
        **{**contract_fields, "ib_local_symbol": str(pos.structure)[:20]},
    )
    db.add(closing_order)
    if entry_order_id is not None:
        await db.flush()  # closing order queryable before folding the reservation
        await recompute_reservation(db, entry_order_id=entry_order_id)

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
        # A limit the operator typed is sent as typed (#492). The engine used
        # to replace it with the live quote, like the automatic price.
        payload["limit_is_operator"] = limit_price_override is not None
    exec_url = get_settings().EXECUTION_URL
    url = f"{exec_url}/internal/positions/close-by-symbol"
    try:
        async with httpx.AsyncClient(timeout=_CLOSE_DISPATCH_TIMEOUT_S) as client:
            r = await client.post(url, json=payload, headers=internal_headers())
    except httpx.HTTPError as e:
        outcome = classify_dispatch_failure(e)
        if outcome == "not_sent":
            # The request never reached the engine: nothing is live at IB, the
            # close is safely dead and the operator can retry.
            # end_order: the row, the leg it reserved (it stayed reserved
            # here) and the ticket's state (#489)
            await end_order(db, closing_order, "rejected",
                            reason=f"execution-engine unreachable: {type(e).__name__}")
            await db.commit()
            raise HTTPException(503, detail={
                "error": "execution_engine_unreachable",
                "position_id": pos.id, "order_id": closing_order.id,
                "trace_id": trace_id,
            }) from e
        # EX-01 (#319): the request reached the engine and the answer never
        # came back. The order may well be live at IB. Stamping it 'rejected'
        # here is what let a retry place a SECOND live close: the stacking
        # guard ignores rejected rows. Keep it in flight instead: state
        # 'submitted' + submitted_at = now, so the guard counts it for the
        # in-flight window and the reconciler / fills handler settle it.
        await mark_submitted(db, closing_order)
        db.add(TradeEvent(
            structure_id=closing_struct.id,
            event_type="close_dispatch_timeout",
            severity="warning",
            description=(
                f"close live#{pos.id}: no answer from execution-engine within "
                f"{_CLOSE_DISPATCH_TIMEOUT_S:.0f}s; order kept in flight, do not retry"
            ),
            payload={
                "live_position_id": pos.id, "order_id": closing_order.id,
                "exc_type": type(e).__name__, "trace_id": trace_id,
            },
        ))
        await db.commit()
        raise HTTPException(504, detail={
            "error": "close_dispatch_timeout",
            "reason": (
                "the close reached the execution engine but its answer timed out; "
                "the order is treated as IN FLIGHT and a retry is refused until it "
                "settles — check the Orders blotter"
            ),
            "position_id": pos.id, "order_id": closing_order.id,
            "trace_id": trace_id,
        }) from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        await end_order(db, closing_order, "rejected", reason=str(detail))
        await db.commit()
        raise HTTPException(r.status_code, detail)

    ib_response = r.json()

    # ── 3. Stamp IB orderId + flip the leg state to 'submitted'. ──
    # mark_submitted re-reads the row under its lock: the engine's handlers
    # are live by now, and a fill they booked while the answer travelled was
    # written over with "submitted" here (#489).
    ib_order_id = ib_response.get("order_id")
    perm_id = ib_response.get("perm_id")
    await mark_submitted(
        db, closing_order,
        ib_order_id=str(ib_order_id) if ib_order_id is not None else None,
        ib_perm_id=str(perm_id) if perm_id is not None else None,
    )
    # The row says what was SENT (#492): the engine prices an option close off
    # the live quote, so the price at the broker is its answer, not our guess.
    sent_price = ib_response.get("limit_price")
    if isinstance(sent_price, (int, float)) and sent_price > 0:
        closing_order.limit_price = float(sent_price)
        closing_order.order_type = "LMT"
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
        "trace_id": current_trace_id(),  # copy into a ticket to trace this close end-to-end
        "ib": ib_response,
    }


@router.post("/{position_id}/close", response_model=rm.ClosePositionResult, response_model_exclude_unset=True, dependencies=[Depends(require_write)])
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


@router.post("/{position_id}/close-manual", response_model=rm.ManualCloseResult, response_model_exclude_unset=True, dependencies=[Depends(require_write)])
async def close_manual(position_id: int, db: DbDep) -> dict[str, Any]:
    """Mark a position for manual close. Step 5 phase 1 = state flip only.

    The actual closing-structure submit + fills will be wired when markets-open
    phase lands (tracked on the GitHub board). For now we record an audit alert.
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


@router.post("/monitor/run-once", response_model=rm.MonitorRunReport, response_model_exclude_unset=True, dependencies=[Depends(require_write)])
async def run_monitor_once() -> dict[str, Any]:
    """Trigger one monitoring cycle on demand. Useful from the dev panel.

    The scheduler in api lifespan owns the recurring loop ; this endpoint
    creates an ad-hoc instance and runs ``run_once`` once.
    """
    sched = build_position_monitor_scheduler()
    return await sched.run_once()
