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
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_write
from api.dependencies import get_db_session, get_redis_client_or_none
from api.orchestration.position_monitor import build_position_monitor_scheduler
from core.execution.reconciliation import classify_break
from core.ledger import LedgerFill, fold_fills, unrealized_pnl
from core.products import product_label_from_symbol
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
from persistence.reservation import recompute_reservation
from shared.contracts import multiplier_for, parse_local_symbol
from shared.trace import current_trace_id, trace_headers

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


@router.get("/book")
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


@router.get("/breaks")
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


@router.get("/structured")
async def list_structured(db: DbDep, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Open positions grouped by the booked ``trade_structure`` — so a Risk
    Reversal reads as ONE 2-leg group labelled from ``structure_type`` +
    ``reference_tenor`` (the values you actually traded), not re-parsed from the
    IB ``localSymbol``. Live marks/greeks are attached from ``open_position`` when
    a leg matches (by ``ib_local_symbol`` or the ``trade_id`` FK). IB-account
    positions not tied to any booked leg are returned separately as ``unlinked``.

    This is the desk view; ``/open`` stays the raw IB mirror.

    Only structures with at least one **actually-open** leg (a linked
    ``open_position`` row) are returned — a purely-``submitted`` structure whose
    legs never filled is an *order*, not a position, and stays in the Orders
    blotter. This keeps Open positions from mixing pending orders with real
    positions. A half-filled structure (e.g. an RR whose put filled but call
    didn't) still shows here, flagged as a naked residual.
    """
    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    # 1. non-closed structures + their entry legs (the desk's own booking record)
    structs = (await db.execute(
        select(TradeStructure).where(TradeStructure.state != "closed")
        .order_by(desc(TradeStructure.created_at)).limit(limit)
    )).scalars().all()
    struct_ids = [s.id for s in structs]
    legs_by_struct: dict[int, list[StructureOrder]] = {}
    symbol_to_struct: dict[str, int] = {}
    if struct_ids:
        legs = (await db.execute(
            select(StructureOrder).where(StructureOrder.structure_id.in_(struct_ids))
            .order_by(StructureOrder.structure_id, StructureOrder.leg_idx)
        )).scalars().all()
        for lg in legs:
            legs_by_struct.setdefault(lg.structure_id, []).append(lg)
            if lg.ib_local_symbol:
                symbol_to_struct[lg.ib_local_symbol] = lg.structure_id

    # 1b. the BOOK (leg_position) — the authority for what each leg holds (I3/I7).
    # A leg is "open" if its book open_qty != 0, EVEN when the netted IB mirror has
    # no row for it: two trades on opposite sides of one contract net to zero at IB,
    # so the mirror can't show either leg — but the book knows both are real. Without
    # this the surviving leg reads as a detached "Vanilla Call" and the netted-away
    # leg vanishes entirely.
    book_by_order: dict[int, LegPosition] = {}
    order_ids = [lg.id for lgs in legs_by_struct.values() for lg in lgs]
    if order_ids:
        book_by_order = {
            int(bp.order_id): bp for bp in (await db.execute(
                select(LegPosition).where(LegPosition.order_id.in_(order_ids))
            )).scalars().all()
        }

    def _book_open_qty(lg: StructureOrder) -> float | None:
        bp = book_by_order.get(lg.id)
        return float(bp.open_qty) if bp and bp.open_qty is not None else None

    def _leg_is_open(lg: StructureOrder, lp: OpenPosition | None) -> bool:
        # Open if the netted mirror links it OR the book holds a non-zero qty.
        if lp is not None:
            return True
        q = _book_open_qty(lg)
        return q is not None and abs(q) > 1e-9

    # 2. live IB mirror → link each row to a structure (FK or leg symbol); the rest is "unlinked"
    positions = (await db.execute(select(OpenPosition))).scalars().all()
    struct_by_id = {s.id: s for s in structs}
    net0 = lambda: {"delta_usd": 0.0, "gamma_usd": 0.0, "vega_usd": 0.0, "theta_usd": 0.0, "pnl_usd": 0.0, "n_linked": 0}  # noqa: E731
    net_by_struct: dict[int, dict[str, float]] = {s.id: net0() for s in structs}
    # Positions grouped BY STRUCTURE (not globally by symbol) : each netted IB
    # position belongs to exactly one structure (its trade_id, set by position_sync),
    # so a leg links only to a position its OWN structure owns — two structures that
    # share the same contract don't cross-claim each other's fill.
    pos_by_struct: dict[int, dict[str, OpenPosition]] = {}
    unlinked: list[dict[str, Any]] = []

    def _marks(p: OpenPosition | None) -> dict[str, Any]:
        return {
            "mark": _f(p.market_price) if p else None, "pnl_usd": _f(p.current_pnl_usd) if p else None,
            "delta_usd": _f(p.delta_usd) if p else None, "gamma_usd": _f(p.gamma_usd) if p else None,
            "vega_usd": _f(p.vega_usd) if p else None, "theta_usd": _f(p.theta_usd) if p else None,
            "vanna_usd": _f(p.vanna_usd) if p else None, "volga_usd": _f(p.volga_usd) if p else None,
            "iv": _f(p.iv) if p else None,
        }

    def _leg_dict(lg: StructureOrder, lp: OpenPosition | None) -> dict[str, Any]:
        # One hydrated leg : DB identity/terms (from the StructureOrder) + the live
        # IB mirror (from the linked open_position). This is the SINGLE payload the
        # Open positions panel renders — no client-side inference, no second fetch.
        return {
            "leg_idx": lg.leg_idx, "contract_type": lg.contract_type, "side": lg.side, "qty": lg.qty,
            "strike": _f(lg.contract_strike),
            "expiry": lg.contract_expiry.isoformat() if lg.contract_expiry else None,
            "state": lg.state, "qty_filled": lg.qty_filled, "ib_local_symbol": lg.ib_local_symbol,
            # linked = THIS structure has a live IB position for this leg's contract.
            # Keyed within the structure's own positions, so a contract shared with
            # another structure can't produce a false "filled".
            "linked": lp is not None,
            # open = the leg is really held per the BOOK (leg_position), independent of
            # whether IB's netted mirror can show it. This is what the panel renders on
            # so a netted leg doesn't vanish. open_qty is the signed book holding.
            "open": _leg_is_open(lg, lp),
            "open_qty": _book_open_qty(lg),
            # Live IB-mirror identity — present only when a real position backs this
            # leg. position_id is the open_position row the UI closes by.
            "position_id": lp.id if lp else None,
            "con_id": lp.contract_id if lp else None,
            "held_qty": _f(lp.quantity) if lp else None,
            "held_side": lp.side if lp else None,
            # entry price: the mirror's when linked, else the book's avg fill price.
            "entry": _f(lp.contract_price_entry) if lp else (
                _f(book_by_order[lg.id].avg_price) if lg.id in book_by_order else None
            ),
            "nominal_eur": _f(lp.nominal_eur) if lp else None,
            "tenor": (lp.tenor if lp and lp.tenor else None),
            "opened": lp.entry_timestamp.isoformat() if (lp and lp.entry_timestamp) else None,
            "updated": lp.timestamp.isoformat() if (lp and lp.timestamp) else None,
            **_marks(lp),
        }

    for p in positions:
        sid = p.trade_id if p.trade_id in struct_by_id else symbol_to_struct.get(p.structure)
        if sid is None:
            unlinked.append({
                "id": p.id, "symbol": p.structure, "product_label": p.product_label,
                "side": p.side, "qty": _f(p.quantity), "tenor": p.tenor,
                "expiry": p.expiry.isoformat() if p.expiry else None, **_marks(p),
            })
            continue
        n = net_by_struct[sid]
        n["delta_usd"] += float(p.delta_usd or 0)
        n["gamma_usd"] += float(p.gamma_usd or 0)
        n["vega_usd"] += float(p.vega_usd or 0)
        n["theta_usd"] += float(p.theta_usd or 0)
        n["pnl_usd"] += float(p.current_pnl_usd or 0)
        n["n_linked"] += 1
        pos_by_struct.setdefault(sid, {})[p.structure] = p

    # Show a structure only if it has LIVE IB presence (>=1 mirror-linked leg) — a
    # fully-netted trade (every leg offset flat at IB by an opposite trade) carries
    # zero live risk and would otherwise flood the panel with all-zero rows. WITHIN a
    # shown structure we still render every book-open leg (below), so a live spread
    # shows both legs including a netted-away sibling — the actual fix.
    out_structs = [{
        "structure_id": s.id, "structure_type": s.structure_type, "product_label": s.product_label,
        "tenor": s.reference_tenor, "state": s.state, "base_qty": s.base_qty,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "legs": [
            _leg_dict(lg, pos_by_struct.get(s.id, {}).get(lg.ib_local_symbol or ""))
            for lg in legs_by_struct.get(s.id, [])
        ],
        "net": net_by_struct[s.id],
    } for s in structs if net_by_struct[s.id]["n_linked"] > 0]
    return {"structures": out_structs, "unlinked": unlinked}


@router.get("/reconciliation")
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


@router.get("/ledger")
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
_MKT_CLOSE_BUFFER = float(os.getenv("MARKETABLE_LIMIT_BUFFER", "0.25"))

# How long a closing order counts as "in flight" for the stacking guard. Past
# this, an unfilled close is treated as stuck/cancelled and no longer blocks a
# fresh close (a cancelled order's DB row isn't always flipped terminal). A real
# option close fills in ~30 s, so 3 min is a generous ceiling.
_INFLIGHT_CLOSE_WINDOW = timedelta(minutes=float(os.getenv("CLOSE_INFLIGHT_WINDOW_MIN", "3")))


def close_inflight_remaining(
    qty: int,
    qty_filled: int | None,
    submitted_at: datetime | None,
    state_updated_at: datetime | None,
    now: datetime,
    window: timedelta,
) -> int:
    """Unfilled qty of a close order that still counts against the stacking guard
    (pure/testable). Returns 0 (self-healed) once the order is older than
    ``window``, so a stuck close never locks the position forever.

    Age is measured from ``submitted_at`` when the order was dispatched, else from
    ``state_updated_at`` — the crucial case: a close CREATED but never dispatched
    (``submitted_at IS NULL``, e.g. the engine was down) keeps a NULL
    ``submitted_at`` indefinitely, so without the ``state_updated_at`` fallback it
    would be counted as in-flight forever and permanently block every new close.
    """
    cutoff = now - window
    age_ts = submitted_at if submitted_at is not None else state_updated_at
    if age_ts is not None and age_ts < cutoff:
        return 0  # stuck / cancelled at IB, or never dispatched — don't block
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
    inflight_conds = [
        StructureOrder.order_role == "closing",
        StructureOrder.side == reverse_side,
        StructureOrder.contract_expiry == pos.expiry,
        StructureOrder.state.in_(("pending", "submitted", "partially_filled")),
    ]
    if strike_val is not None:
        inflight_conds.append(StructureOrder.contract_strike == strike_val)
    # Sum the still-in-flight remaining qty in Python via the pure predicate — the
    # self-heal boundary (incl. the never-dispatched submitted_at IS NULL case)
    # lives in one tested place instead of drifting inside a SQL expression.
    close_orders = (await db.execute(
        select(StructureOrder).where(*inflight_conds)
    )).scalars().all()
    already_closing = sum(
        close_inflight_remaining(
            o.qty, o.qty_filled, o.submitted_at, o.state_updated_at,
            now, _INFLIGHT_CLOSE_WINDOW,
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
        **contract_fields,
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
    exec_url = os.getenv("EXECUTION_URL", "http://execution-engine:8001")
    url = f"{exec_url}/internal/positions/close-by-symbol"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload, headers=trace_headers())
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
        "trace_id": current_trace_id(),  # copy into a ticket to trace this close end-to-end
        "ib": ib_response,
    }


@router.post("/{position_id}/close", dependencies=[Depends(require_write)])
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


@router.post("/{position_id}/close-manual", dependencies=[Depends(require_write)])
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


@router.post("/monitor/run-once", dependencies=[Depends(require_write)])
async def run_monitor_once() -> dict[str, Any]:
    """Trigger one monitoring cycle on demand. Useful from the dev panel.

    The scheduler in api lifespan owns the recurring loop ; this endpoint
    creates an ad-hoc instance and runs ``run_once`` once.
    """
    sched = build_position_monitor_scheduler()
    return await sched.run_once()
