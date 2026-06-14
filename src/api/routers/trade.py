"""Step 3 — Trade preview API.

POST /api/v1/trade/preview              build full preview from a signal_id
POST /api/v1/trade/preview/{id}/cancel  user cancels a pending preview
GET  /api/v1/trade/preview/{id}         retrieve a stored preview
GET  /api/v1/trade/structures           list structure_definitions (catalogue)
GET  /api/v1/trade/limits               list current risk_limits
GET  /api/v1/trade/book                 current book state snapshot
"""
from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis_client_or_none
from api.orchestration.book_state_refresh import refresh_book_state
from core.execution.revalidation import revalidate_preview
from core.execution.slippage import compute_limit_price
from core.trade_preview import (
    build_structure,
    compute_legs_greeks,
    compute_net_greeks,
    compute_pnl_grid,
    compute_sizing,
    parse_recommendation,
    price_structure,
    run_pre_submit_checks,
    simulate_scenarios,
)
from core.trade_preview_regime import apply_regime_to_limits, regime_label
from persistence.models import (
    AppConfigScalar,
    BookStateSnapshot,
    ExecutionAuditLog,
    IbConnectionState,
    PcaSignal,
    RegimeSnapshot,
    StructureDefinition,
    StructureFill,
    StructureOrder,
    TradePosition,
    TradePreviewRow,
    TradeStructure,
)

router = APIRouter(prefix="/api/v1/trade", tags=["trade"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]


class PreviewRequest(BaseModel):
    signal_id: int | None = None
    scenario: str | None = None             # fixture mode (returns canned preview)
    structure_type: str | None = None       # manual mode (no signal needed)
    tenor: str | None = None                # manual mode reference tenor
    tenor_far: str | None = None            # manual mode (calendar only)
    qty: int | None = None                  # manual mode base_qty
    delta_pillar: str | None = None         # single-leg only : override pillar
    strike_override: float | None = None    # single-leg only : override strike
    override_tenor: str | None = None
    override_far_tenor: str | None = None
    override_qty: int | None = None


async def _load_limits(db: AsyncSession) -> dict[str, float]:
    # risk_limits rows folded into config_scalar with namespace='risk'
    # (migration 033). Same dict[name, value] return shape.
    rows = (await db.execute(
        select(AppConfigScalar.name, AppConfigScalar.value)
        .where(AppConfigScalar.namespace == "risk")
        .where(AppConfigScalar.is_active.is_(True))
    )).all()
    return {name: float(value) for name, value in rows}


async def _load_book(db: AsyncSession, symbol: str, capital_default: float) -> BookStateSnapshot:
    """Return the current book row, or bootstrap an empty in-memory one."""
    row = (await db.execute(
        select(BookStateSnapshot)
        .where(BookStateSnapshot.symbol == symbol)
        .where(BookStateSnapshot.is_current.is_(True))
        .limit(1)
    )).scalar_one_or_none()
    if row:
        return row
    return BookStateSnapshot(
        symbol=symbol, total_vega_usd=0.0, total_gamma_usd=0.0,
        total_theta_usd=0.0, total_delta=0.0,
        n_open_structures=0, n_open_legs=0,
        notional_engaged_usd=0.0, capital_total_usd=capital_default,
        margin_used_usd=0.0, is_current=True,
    )


async def _load_regime(
    db: AsyncSession, symbol: str = "EURUSD",
) -> dict[str, Any] | None:
    """Latest regime snapshot, normalised for trade_preview/sizing helpers.

    Returns ``None`` when no regime row exists yet (e.g. fresh DB) so the
    pre-submit regime gate evaluates as ``calm`` (the fallback used by
    ``run_pre_submit_checks``).
    """
    row = (await db.execute(
        select(RegimeSnapshot)
        .where(RegimeSnapshot.symbol == symbol)
        .order_by(desc(RegimeSnapshot.timestamp))
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None
    return {"label": row.label, "event_dampener": bool(row.event_dampener)}


async def _fetch_ib_connected(db: AsyncSession) -> bool:
    """Read latest cached value of ``runtime_ib_session.is_connected``.

    Heartbeat loop in execution-engine refreshes this row every 10 s.
    Used as pre-condition for /trade/submit when execution_mode='live'.
    """
    row = (await db.execute(
        select(IbConnectionState).where(IbConnectionState.broker == "IB").limit(1)
    )).scalar_one_or_none()
    return bool(row.is_connected) if row is not None else False


async def _acquire_preview_lock(preview_id: str, ttl_s: int = 10) -> bool:
    """Best-effort Redis lock keyed by preview_id (cf. STEP4 §7.2 decision 6).

    Returns True if the lock was acquired (caller proceeds to submit), False
    if another concurrent submit holds it. Falls back to True if Redis is
    unavailable — lock is defense-in-depth, not the only safeguard
    (`preview.user_action` is updated under DB transaction).
    """
    client = get_redis_client_or_none()
    if client is None:
        return True
    key = f"trade:submit_lock:{preview_id}"
    try:
        ok = await client.set(key, b"1", ex=ttl_s, nx=True)
        return bool(ok)
    except Exception:
        return True


async def _post_execution_engine(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """HTTP POST to execution-engine on the internal Docker network.

    URL via ``EXECUTION_ENGINE_URL`` (default ``http://execution-engine:8001``).
    Failures bubble up as 502 — the structure rows are already persisted, so
    operator inspects + retries via ``POST /internal/structure/submit``.
    """
    import os

    import httpx
    base = os.environ.get("EXECUTION_ENGINE_URL", "http://execution-engine:8001")
    url = f"{base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            raise HTTPException(
                502,
                detail={
                    "error": "execution_engine_failed",
                    "status": resp.status_code,
                    "body": resp.text[:500],
                },
            )
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            502,
            detail={"error": "execution_engine_unreachable", "exception": str(e)[:300]},
        ) from e


async def _read_surface_redis(symbol: str = "EURUSD") -> tuple[dict[str, Any] | None, float]:
    """Read latest_vol_surface from Redis. Returns (surface_dict, age_seconds)."""
    try:
        from api.dependencies import get_redis_client_or_none
        client = get_redis_client_or_none()
        if client is None:
            return None, 999.0
        raw = await client.get(f"latest_vol_surface:{symbol}")
        if not raw:
            return None, 999.0
        import json
        payload = json.loads(raw)
        surface = payload.get("surface") or payload
        ts_str = payload.get("timestamp")
        age = 999.0
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = (datetime.now(UTC) - ts).total_seconds()
            except Exception:
                pass
        return surface, age
    except Exception:
        return None, 999.0


@router.get("/structures")
async def list_structures(db: DbDep) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(StructureDefinition).where(StructureDefinition.is_active.is_(True))
    )).scalars().all()
    return [
        {
            "structure_type": r.structure_type, "display_name": r.display_name,
            "leg_template": r.leg_template, "min_legs": r.min_legs, "max_legs": r.max_legs,
            "requires_delta_hedge": r.requires_delta_hedge,
            "typical_vega_sign": r.typical_vega_sign,
            "typical_gamma_sign": r.typical_gamma_sign,
            "typical_theta_sign": r.typical_theta_sign,
            "rationale_for_pc": r.rationale_for_pc,
        } for r in rows
    ]


@router.get("/limits")
async def list_limits(db: DbDep) -> dict[str, dict[str, Any]]:
    # risk_limits rows folded into config_scalar with namespace='risk' (migration 033).
    rows = (await db.execute(
        select(AppConfigScalar)
        .where(AppConfigScalar.namespace == "risk")
        .where(AppConfigScalar.is_active.is_(True))
    )).scalars().all()
    return {r.name: {"value": float(r.value), "unit": r.unit, "description": r.description} for r in rows}


@router.get("/book")
async def get_book(db: DbDep, symbol: str = "EURUSD") -> dict[str, Any]:
    limits = await _load_limits(db)
    book = await _load_book(db, symbol, limits.get("starting_capital_usd", 100000.0))
    return {
        "symbol": book.symbol,
        "total_vega_usd": book.total_vega_usd, "total_gamma_usd": book.total_gamma_usd,
        "total_theta_usd": book.total_theta_usd, "total_delta": book.total_delta,
        "n_open_structures": book.n_open_structures, "n_open_legs": book.n_open_legs,
        "notional_engaged_usd": book.notional_engaged_usd,
        "capital_total_usd": book.capital_total_usd,
        "margin_used_usd": book.margin_used_usd,
        "is_current": book.is_current,
        "is_bootstrap": book.id is None,
    }


@router.post("/preview")
async def create_preview(req: PreviewRequest, db: DbDep, symbol: str = Query("EURUSD")) -> dict[str, Any]:
    """Build a full trade preview from a pca_signals.id. Persists to trade_previews
    and returns the payload conforming to STEP3 §4."""

    # Fixture mode : return a canned preview without touching live data.
    if req.scenario:
        return _fixture_preview(req.scenario)

    # Manual mode : user picked structure + tenor directly (no PCA signal).
    manual_mode = req.signal_id is None and req.structure_type is not None
    if not manual_mode and not req.signal_id:
        raise HTTPException(400, "signal_id or structure_type required (or use ?scenario=…)")

    signal = None
    if not manual_mode:
        signal = (await db.execute(
            select(PcaSignal).where(PcaSignal.id == req.signal_id).limit(1)
        )).scalar_one_or_none()
        if signal is None:
            raise HTTPException(404, "signal not found")
        if not signal.actionable:
            raise HTTPException(
                400, f"signal not actionable: {signal.actionable_reason or 'unknown'}",
            )
        if not signal.recommended_structure:
            raise HTTPException(400, "signal has no recommended_structure")

    # 2. Limits + book + surface + regime. Regime conditions both the
    #    sizing multiplier (compute_sizing) and the *limits* themselves
    #    (apply_regime_to_limits → tightens max_book_vega in stressed,
    #    collapses to zero in pre_event).
    raw_limits = await _load_limits(db)
    regime = await _load_regime(db, symbol=symbol)
    limits = apply_regime_to_limits(raw_limits, regime)
    book = await _load_book(db, symbol, limits.get("starting_capital_usd", 100000.0))
    surface, surface_age_s = await _read_surface_redis(symbol)
    if surface is None:
        # In manual mode markets-closed sandbox, fall back to a synthetic surface
        # so the user can still exercise the UX. Real flow needs Redis surface.
        if manual_mode:
            surface = _synthetic_surface()
            surface_age_s = 0.0
        else:
            raise HTTPException(503, "surface unavailable (vol-engine down or markets closed)")

    # 3. Parse recommendation (signal mode) or use manual inputs
    if manual_mode:
        structure_type = req.structure_type
        near_tenor = (req.tenor or "3M").upper()
        far_tenor = req.tenor_far.upper() if req.tenor_far else None
    else:
        structure_type, near_tenor, far_tenor = parse_recommendation(signal.recommended_structure)
    if req.override_tenor:
        near_tenor = req.override_tenor.upper()
    if req.override_far_tenor:
        far_tenor = req.override_far_tenor.upper()

    structure = build_structure(
        structure_type, near_tenor, far_tenor, surface,
        delta_pillar_override=req.delta_pillar,
        strike_override=req.strike_override,
    )
    pricing = price_structure(structure, surface)
    greeks = compute_net_greeks(structure, surface)

    # 4. Sizing
    if manual_mode:
        # No conviction multiplier, no book penalty — just use the qty the user
        # set explicitly. Equivalent to base_qty=qty and z_factor=1.
        base_qty_eff = int(req.qty or limits.get("base_qty", 10))
        sizing = compute_sizing(
            z_score=1.5,                                 # = threshold_min so z_factor=1
            structure=structure,
            total_premium=pricing.total_premium_usd,
            book_total_vega_usd=0.0,
            book_vega_neutral_threshold=limits.get("book_vega_neutral_threshold", 2000.0),
            base_qty=base_qty_eff,
            threshold_min=limits.get("z_threshold_min", 1.5),
            max_z_multiplier=1.0,                        # cap at 1
            book_alpha=0.0,
            regime=regime,
            qty_override=base_qty_eff,                   # force exact qty
        )
    else:
        sizing = compute_sizing(
            z_score=float(signal.z_score),
            structure=structure,
            total_premium=pricing.total_premium_usd,
            book_total_vega_usd=float(book.total_vega_usd),
            book_vega_neutral_threshold=limits.get("book_vega_neutral_threshold", 2000.0),
            base_qty=int(limits.get("base_qty", 10)),
            threshold_min=limits.get("z_threshold_min", 1.5),
            max_z_multiplier=limits.get("max_z_multiplier", 2.0),
            book_alpha=limits.get("book_alpha", 0.3),
            regime=regime,
            qty_override=req.override_qty,
        )

    # 5. Scenarios + per-leg greeks + 2D P&L grid (risk-analysis tables)
    scenarios = simulate_scenarios(structure, surface, greeks)
    legs_greeks = compute_legs_greeks(structure, surface)
    pnl_grid = compute_pnl_grid(structure, surface, greeks)

    # 6. Pre-submit checks
    structure_vega_at_size = greeks.vega_usd_per_volpt * (sizing.final_qty_per_leg / max(sizing.base_qty, 1))
    sized_max_loss = pricing.max_loss_usd * (sizing.final_qty_per_leg / max(sizing.base_qty, 1))

    # latest signal under same model + pc_id (for "still actionable" gate)
    if manual_mode:
        # Manual mode : skip the signal_still_actionable gate by using
        # equal armed/current z above threshold.
        armed_z_for_check = 2.0
        current_z = 2.0
    else:
        latest = (await db.execute(
            select(PcaSignal)
            .where(PcaSignal.pca_model_id == signal.pca_model_id)
            .where(PcaSignal.pc_id == signal.pc_id)
            .order_by(desc(PcaSignal.timestamp))
            .limit(1)
        )).scalar_one_or_none()
        armed_z_for_check = float(signal.z_score)
        current_z = float(latest.z_score) if latest else armed_z_for_check

    checks = run_pre_submit_checks(
        regime=regime,
        armed_z=armed_z_for_check, current_z=current_z,
        threshold_min=limits.get("z_threshold_min", 1.5),
        max_loss_usd=sized_max_loss,
        capital_total_usd=float(book.capital_total_usd or limits.get("starting_capital_usd", 100000.0)),
        max_loss_pct=limits.get("max_loss_per_trade_pct", 2.0),
        book_total_vega_usd=float(book.total_vega_usd),
        structure_vega_usd=structure_vega_at_size,
        max_book_vega_usd=limits.get("max_book_vega_usd", 5000.0),
        surface_age_seconds=surface_age_s,
        max_iv_age_s=limits.get("max_iv_data_age_seconds", 120),
        has_arb_violation=False,            # MVP : no arb check on synth surface
        min_quoted_size=99,                 # MVP : assume liquid
        min_liquidity=int(limits.get("min_liquidity_quoted_size", 10)),
    )

    blocking = [c.name for c in checks if not c.passed]
    state = "blocked" if blocking else "valid_for_submit"

    # Costs : commission + total trade cost. Commission = $/contract × total
    # contracts (sum across legs, weighted by qty_factor). Premium per contract
    # is the structure premium for qty=base_qty divided by base_qty.
    commission_per_contract = limits.get("commission_per_contract_usd", 2.0)
    total_contracts = sum(abs(q) for q in sizing.leg_quantities.values())
    commission_usd = float(total_contracts) * commission_per_contract
    premium_per_contract_usd = (
        pricing.total_premium_usd / max(sizing.base_qty, 1)
        if sizing.base_qty else 0.0
    )
    total_trade_cost_usd = abs(sizing.final_premium_usd) + commission_usd

    preview_id = "tp_" + secrets.token_hex(6)
    expires_at = datetime.now(UTC) + timedelta(seconds=int(limits.get("preview_validity_seconds", 120)))

    payload = {
        "preview_id": preview_id,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": expires_at.isoformat(),
        "mode": "manual" if manual_mode else "from_signal",
        "signal_source": None if manual_mode else {
            "signal_id": signal.id, "pca_model_id": signal.pca_model_id,
            "triggering_pc": signal.pc_id, "z_score": float(signal.z_score),
            "label": signal.label,
        },
        "structure": {
            "type": structure.type,
            "reference_tenor": structure.reference_tenor,
            "tenor_far": structure.tenor_far,
            "requires_delta_hedge": structure.requires_delta_hedge,
            "vega_sign": structure.vega_sign,
            "legs": [
                {
                    "leg_idx": leg.leg_idx, "contract_type": leg.contract_type,
                    "tenor": leg.tenor, "expiry": leg.expiry, "dte": leg.dte,
                    "strike": leg.strike, "qty_factor": leg.qty_factor,
                    "qty": sizing.leg_quantities.get(leg.leg_idx, 0),
                    "side": leg.side, "entry_iv_pct": leg.entry_iv_pct,
                    "entry_price_per_contract_usd": pricing.leg_prices_usd[leg.leg_idx]
                                                     if leg.leg_idx < len(pricing.leg_prices_usd) else 0.0,
                } for leg in structure.legs
            ],
        },
        "greeks_net": {
            "vega_usd_per_volpt": greeks.vega_usd_per_volpt,
            "gamma_usd_per_pip2": greeks.gamma_usd_per_pip2,
            "theta_usd_per_day": greeks.theta_usd_per_day,
            "delta_unhedged": greeks.delta_unhedged,
            "delta_post_hedge": greeks.delta_post_hedge,
        },
        "pricing": {
            "premium_paid_usd": pricing.total_premium_usd,
            "max_loss_usd": pricing.max_loss_usd,
            "max_loss_at_expiry_only": pricing.max_loss_at_expiry_only,
            "breakeven_pips_each_side": pricing.breakeven_pips_each_side,
        },
        "costs": {
            "premium_per_contract_usd": round(premium_per_contract_usd, 4),
            "commission_usd": round(commission_usd, 2),
            "total_trade_cost_usd": round(total_trade_cost_usd, 2),
        },
        "scenarios": scenarios,
        "legs_greeks": legs_greeks,
        "pnl_grid": pnl_grid,
        "sizing": {
            "base_qty": sizing.base_qty,
            "multipliers": sizing.multipliers,
            "final_qty_per_leg": sizing.final_qty_per_leg,
            "leg_quantities": sizing.leg_quantities,
            "final_premium_usd": sizing.final_premium_usd,
            "sizing_formula": sizing.sizing_formula,
        },
        "pre_submit_checks": [
            {"name": c.name, "passed": c.passed, "details": c.details} for c in checks
        ],
        "state": state,
        "blocking_reasons": blocking,
        "surface_age_seconds": surface_age_s,
    }

    # 7. Persist (audit)
    row = TradePreviewRow(
        preview_id=preview_id, expires_at=expires_at,
        pca_signal_id=signal.id if signal else None,
        triggering_pc=signal.pc_id if signal else None,
        armed_z_score=float(signal.z_score) if signal else None,
        armed_signal_label=signal.label if signal else None,
        structure_type=structure.type, reference_tenor=structure.reference_tenor,
        structure_full_payload=payload, state=state,
        pre_submit_checks=payload["pre_submit_checks"],
        blocking_reasons=blocking or None,
    )
    db.add(row)
    await db.commit()
    return payload


def _synthetic_surface() -> dict[str, Any]:
    """Fallback surface for sandbox manual-preview when Redis is empty.

    Centred at 1.0850 spot, ATM σ ≈ 7%, mild smile. Strike spread mimics
    delta pillars but is not arbitrage-checked — for UX testing only.
    """
    spot = 1.0850
    pillars = [("10dp", 0.6, -0.020), ("25dp", 0.2, -0.010),
               ("atm", 0.0, 0.0), ("25dc", 0.1, 0.010), ("10dc", 0.45, 0.020)]
    out: dict[str, Any] = {}
    base_atm = {"1M": 0.068, "2M": 0.069, "3M": 0.070,
                "4M": 0.0705, "5M": 0.071, "6M": 0.0715}
    for tenor, atm in base_atm.items():
        out[tenor] = {
            d: {"iv": atm + smile / 100.0, "strike": spot + offset}
            for (d, smile, offset) in pillars
        }
    return out


@router.get("/preview/{preview_id}")
async def get_preview(preview_id: str, db: DbDep) -> dict[str, Any]:
    row = (await db.execute(
        select(TradePreviewRow).where(TradePreviewRow.preview_id == preview_id).limit(1)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "preview not found")
    return {
        **(row.structure_full_payload or {}),
        "state": row.state,
        "user_action": row.user_action,
    }


@router.post("/submit")
async def submit_preview(
    body: dict[str, Any], db: DbDep,
) -> dict[str, Any]:
    """Step 4 — Submit a previewed trade. **MOCK EXECUTION**.

    No IB call is made. Instead the endpoint :
      1. Loads the preview row.
      2. Creates a ``trade_structures`` row with state='submitted'.
      3. Creates ``structure_orders`` rows (one per leg) with state='filled'.
      4. Creates ``structure_fills`` rows with synthetic fill prices == preview prices
         (zero slippage, $2/contract commission).
      5. Marks structure ``fully_filled`` and creates a ``trade_positions`` row.
      6. Marks the trade_preview as ``user_action='submitted'``.

    Returns the structure summary including position_id. Real IB integration
    is deferred to spec phase 2/3.
    """
    preview_id = body.get("preview_id")
    if not preview_id:
        raise HTTPException(400, "preview_id required")

    # execution_mode : 'mock' (default — synthetic fills, no IB) or 'live'
    # ('live' currently no-ops past the gating check ; real ib_insync wiring
    # arrives in Passe B). The gate on `is_connected` is enforced for live.
    execution_mode = str(body.get("execution_mode", "mock")).lower()
    if execution_mode not in ("mock", "live"):
        raise HTTPException(400, f"invalid execution_mode: {execution_mode}")

    # Defense-in-depth lock to prevent double-submit (e.g. user double-clicks
    # or retry mid-network). DB-level guard (preview.user_action) is the
    # source of truth — this just short-circuits the duplicate request faster.
    if not await _acquire_preview_lock(preview_id):
        raise HTTPException(409, "submit already in progress for this preview")

    preview = (await db.execute(
        select(TradePreviewRow).where(TradePreviewRow.preview_id == preview_id).limit(1)
    )).scalar_one_or_none()
    if preview is None:
        raise HTTPException(404, "preview not found")

    # Live mode requires IB Gateway up. Mock mode skips the gate.
    if execution_mode == "live" and not await _fetch_ib_connected(db):
        db.add(ExecutionAuditLog(
            structure_id=None, event_type="submission_blocked",
            severity="warning", message="ib_disconnected_at_submit",
            payload={"preview_id": preview_id},
        ))
        await db.commit()
        raise HTTPException(503, detail={
            "error": "ib_disconnected",
            "reason": "IB Gateway not connected — heartbeat stale",
        })

    # Defense-in-depth revalidation. Mirror gates that may have flipped between
    # Arm and Submit (preview can sit ~120s). Surface freshness and signal-
    # actionability are checked against current PCA state.
    payload_for_revalidation = preview.structure_full_payload or {}
    armed_z_revalidate: float | None = None
    current_z_revalidate: float | None = None
    if preview.armed_z_score is not None:
        armed_z_revalidate = float(preview.armed_z_score)
        if preview.pca_signal_id is not None:
            latest = (await db.execute(
                select(PcaSignal)
                .where(PcaSignal.id == preview.pca_signal_id)
                .limit(1)
            )).scalar_one_or_none()
            if latest is not None:
                latest_for_pc = (await db.execute(
                    select(PcaSignal)
                    .where(PcaSignal.pca_model_id == latest.pca_model_id)
                    .where(PcaSignal.pc_id == latest.pc_id)
                    .order_by(desc(PcaSignal.timestamp))
                    .limit(1)
                )).scalar_one_or_none()
                if latest_for_pc is not None:
                    current_z_revalidate = float(latest_for_pc.z_score)
        if current_z_revalidate is None:
            current_z_revalidate = armed_z_revalidate
    surface_age = float(payload_for_revalidation.get("surface_age_seconds") or 0.0)
    limits_for_revalidation = await _load_limits(db)
    regime_for_revalidation = await _load_regime(db)
    revalidation = revalidate_preview(
        preview_state=preview.state,
        preview_user_action=preview.user_action,
        preview_expires_at=preview.expires_at,
        now=datetime.now(UTC),
        armed_z=armed_z_revalidate,
        current_z=current_z_revalidate,
        z_threshold_min=limits_for_revalidation.get("z_threshold_min", 1.5),
        surface_age_seconds=surface_age,
        max_iv_age_seconds=limits_for_revalidation.get("max_iv_data_age_seconds", 120.0),
        current_regime=regime_label(regime_for_revalidation) if regime_for_revalidation else None,
    )
    if not revalidation.passed:
        # Audit-log the block + return structured error (status 400 — not 422 —
        # since the client already passed body validation, the issue is state).
        db.add(ExecutionAuditLog(
            structure_id=None, event_type="submission_blocked",
            severity="warning", message=f"revalidation_failed: {revalidation.reason}",
            payload=revalidation.details,
        ))
        await db.commit()
        raise HTTPException(
            400,
            detail={
                "error": "revalidation_failed",
                "reason": revalidation.reason,
                "details": revalidation.details,
            },
        )

    payload = preview.structure_full_payload or {}
    legs = (payload.get("structure") or {}).get("legs") or []
    if not legs:
        raise HTTPException(400, "preview has no legs")

    sizing = payload.get("sizing") or {}
    base_qty = int(sizing.get("base_qty", 1))
    limits_for_submit = await _load_limits(db)
    commission_per_contract = limits_for_submit.get("commission_per_contract_usd", 2.0)
    slippage_tolerance_pct = float(
        limits_for_submit.get("slippage_tolerance_pct", 0.5)
    )

    # Pick the first non-null expiry across legs as structure expiry.
    expiry_iso: str | None = next(
        (leg.get("expiry") for leg in legs if leg.get("expiry")), None,
    )
    expiry_d: date | None = None
    if expiry_iso:
        try:
            expiry_d = date.fromisoformat(expiry_iso)
        except ValueError:
            pass

    # 1. Create structure (state=submitted initially, flipped to fully_filled below)
    structure = TradeStructure(
        preview_id=preview_id,
        pca_signal_id=preview.pca_signal_id,
        triggering_pc=preview.triggering_pc,
        armed_z_score=preview.armed_z_score,
        armed_signal_label=preview.armed_signal_label,
        structure_type=preview.structure_type,
        reference_tenor=preview.reference_tenor,
        expiry_date=expiry_d,
        base_qty=base_qty,
        state="submitted",
        execution_mode=execution_mode,
    )
    db.add(structure)
    await db.flush()

    db.add(ExecutionAuditLog(
        structure_id=structure.id, event_type="submission_attempt",
        severity="info",
        message=f"{execution_mode} submit for preview {preview_id}",
    ))

    now = datetime.now(UTC)

    # ── LIVE PATH ──────────────────────────────────────────────────────
    # Persist orders in 'pending' state, then call execution-engine which
    # places them via ib_insync and wires fills handlers. The cascade to
    # state='filled' / 'fully_filled' / trade_positions arrives via events.
    if execution_mode == "live":
        for i, leg in enumerate(legs):
            qty = int(leg.get("qty", 0))
            preview_price = float(leg.get("entry_price_per_contract_usd") or 0.0)
            side = leg.get("side", "BUY")
            contract_type = leg.get("contract_type", "call")
            contract_strike = leg.get("strike")
            contract_expiry: date | None = None
            if leg.get("expiry"):
                try:
                    contract_expiry = date.fromisoformat(leg["expiry"])
                except ValueError:
                    pass
            try:
                limit_price = compute_limit_price(
                    preview_price=preview_price, side=side,
                    slippage_tolerance_pct=slippage_tolerance_pct,
                )
            except ValueError:
                limit_price = preview_price
            db.add(StructureOrder(
                structure_id=structure.id, leg_idx=i, order_role="entry",
                contract_type=contract_type, contract_expiry=contract_expiry,
                contract_strike=float(contract_strike)
                                 if isinstance(contract_strike, (int, float)) else None,
                side=side, qty=qty,
                order_type="LMT", limit_price=limit_price,
                preview_iv_pct=leg.get("entry_iv_pct"),
                preview_price=preview_price,
                state="pending",
            ))
        preview.user_action = "submitted"
        preview.user_action_at = now
        preview.state = "submitted"
        await db.commit()

        # Fire-and-forget HTTP call to execution-engine. Failure here does
        # not roll the structure back automatically — operator decides.
        ee_result = await _post_execution_engine(
            "/internal/structure/submit",
            {"structure_id": structure.id},
        )
        return {
            "success": True,
            "structure_id": structure.id,
            "position_id": None,                   # arrives via fill cascade
            "n_orders_submitted": len(legs),
            "execution_mode": "live",
            "state": "submitted",
            "execution_engine": ee_result,
            "fully_filled_at": None,
        }

    # ── MOCK PATH ──────────────────────────────────────────────────────
    # 2. Create one structure_orders row per leg + one structure_fills with
    #    synthetic price (= preview price, no slippage).
    total_premium = 0.0
    total_commission = 0.0
    total_slippage = 0.0
    for i, leg in enumerate(legs):
        qty = int(leg.get("qty", 0))
        preview_price = float(leg.get("entry_price_per_contract_usd") or 0.0)
        side = leg.get("side", "BUY")
        contract_type = leg.get("contract_type", "call")
        contract_strike = leg.get("strike")
        contract_expiry: date | None = None
        if leg.get("expiry"):
            try:
                contract_expiry = date.fromisoformat(leg["expiry"])
            except ValueError:
                pass

        # Limit price = preview_price ± slippage tolerance (BUY caps above,
        # SELL floors below). Helper compute_limit_price is pure (cf. spec
        # §13 decision 7). Used in both mock and live — in mock the synthetic
        # fill still happens at preview_price (zero slippage), but the limit
        # we'd send to IB is logged.
        try:
            limit_price = compute_limit_price(
                preview_price=preview_price, side=side,
                slippage_tolerance_pct=slippage_tolerance_pct,
            )
        except ValueError:
            limit_price = preview_price

        order = StructureOrder(
            structure_id=structure.id, leg_idx=i,
            ib_order_id=f"mock_{structure.id}_{i}",
            ib_perm_id=f"mock_perm_{structure.id}_{i}",
            contract_type=contract_type,
            contract_expiry=contract_expiry,
            contract_strike=float(contract_strike) if isinstance(contract_strike, (int, float)) else None,
            side=side, qty=qty,
            order_type="LMT", limit_price=limit_price,
            preview_iv_pct=leg.get("entry_iv_pct"),
            preview_price=preview_price,
            state="filled",
            submitted_at=now, acknowledged_at=now, fully_filled_at=now,
            qty_filled=qty,
            avg_fill_price=preview_price,
            slippage_per_contract=0.0,
            total_slippage_usd=0.0,
            total_commission_usd=qty * commission_per_contract,
        )
        db.add(order)
        await db.flush()

        # Synthetic fill row
        fill = StructureFill(
            order_id=order.id,
            ib_execution_id=f"mock_exec_{structure.id}_{i}",
            timestamp=now, qty_filled=qty, fill_price=preview_price,
            commission_usd=qty * commission_per_contract,
            exchange="MOCK", side=side,
        )
        db.add(fill)

        sign = +1 if side == "BUY" else -1
        total_premium += sign * preview_price * qty
        total_commission += qty * commission_per_contract

    # 3. Mark structure fully_filled + aggregate
    structure.state = "fully_filled"
    structure.fully_filled_at = now
    structure.first_fill_at = now
    structure.total_premium_paid_usd = round(total_premium, 4)
    structure.total_slippage_usd = round(total_slippage, 4)
    structure.total_commission_usd = round(total_commission, 2)
    structure.total_entry_cost_usd = round(total_slippage + total_commission, 2)

    # 4. Create position record (consumed by Step 5)
    greeks = payload.get("greeks_net") or {}
    position = TradePosition(
        structure_id=structure.id,
        opened_at=now,
        entry_premium_usd=structure.total_premium_paid_usd or 0.0,
        entry_total_cost_usd=structure.total_entry_cost_usd or 0.0,
        state="open",
        entry_vega_usd_per_volpt=greeks.get("vega_usd_per_volpt"),
        entry_gamma_usd_per_pip2=greeks.get("gamma_usd_per_pip2"),
        entry_theta_usd_per_day=greeks.get("theta_usd_per_day"),
    )
    db.add(position)
    await db.flush()

    # 5. Mark preview as submitted
    preview.user_action = "submitted"
    preview.user_action_at = now
    preview.state = "submitted"

    db.add(ExecutionAuditLog(
        structure_id=structure.id, event_type="structure_filled",
        severity="info", message="mock fully_filled, position created",
        payload={"position_id": position.id, "premium_usd": structure.total_premium_paid_usd},
    ))

    # Refresh the singleton book_state row so the next sizing call sees an
    # up-to-date total_vega (book_alpha penalty otherwise stays dead at 0).
    await refresh_book_state(db)

    await db.commit()

    return {
        "success": True,
        "structure_id": structure.id,
        "position_id": position.id,
        "n_orders_submitted": len(legs),
        "execution_mode": execution_mode,
        "state": structure.state,
        "total_premium_paid_usd": structure.total_premium_paid_usd,
        "total_commission_usd": structure.total_commission_usd,
        "total_entry_cost_usd": structure.total_entry_cost_usd,
        "fully_filled_at": structure.fully_filled_at.isoformat() if structure.fully_filled_at else None,
    }


@router.get("/submitted")
async def list_submitted_structures(
    db: DbDep, limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List recent submitted trade_structures (for the new positions tab)."""
    rows = (await db.execute(
        select(TradeStructure).order_by(desc(TradeStructure.created_at)).limit(limit)
    )).scalars().all()
    out: list[dict[str, Any]] = []
    for s in rows:
        position = (await db.execute(
            select(TradePosition).where(TradePosition.structure_id == s.id).limit(1)
        )).scalar_one_or_none()
        out.append({
            "id": s.id, "created_at": s.created_at, "structure_type": s.structure_type,
            "reference_tenor": s.reference_tenor, "base_qty": s.base_qty, "state": s.state,
            "execution_mode": s.execution_mode,
            "total_premium_paid_usd": s.total_premium_paid_usd,
            "total_commission_usd": s.total_commission_usd,
            "total_entry_cost_usd": s.total_entry_cost_usd,
            "preview_id": s.preview_id,
            "position_id": position.id if position else None,
            "position_state": position.state if position else None,
        })
    return out


@router.post("/preview/{preview_id}/cancel")
async def cancel_preview(preview_id: str, db: DbDep) -> dict[str, Any]:
    row = (await db.execute(
        select(TradePreviewRow).where(TradePreviewRow.preview_id == preview_id).limit(1)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "preview not found")
    if row.user_action is not None:
        raise HTTPException(400, f"already actioned: {row.user_action}")
    row.user_action = "cancelled"
    row.user_action_at = datetime.now(UTC)
    row.state = "cancelled"
    await db.commit()
    return {"preview_id": preview_id, "state": "cancelled"}


# ────────────────────────────────────────────────────────────────
# Fixture mode — canned previews for UI testing
# ────────────────────────────────────────────────────────────────


def _fixture_preview(name: str) -> dict[str, Any]:
    """Return a canned trade preview for UI testing without live signals.

    Available scenarios :
      - valid_long_straddle    : full green, ready for submit
      - blocked_max_loss       : sized too big → max_loss_under_capital_limit fails
      - blocked_signal_flipped : armed_z=+2 but current_z=-0.8 → signal_still_actionable fails
      - blocked_stale_data     : surface_age=300s → iv_data_fresh fails
    """
    base_legs_straddle = [
        {"leg_idx": 0, "contract_type": "call", "tenor": "3M", "expiry": "2026-08-02", "dte": 90,
         "strike": 1.0850, "qty_factor": 1, "qty": 12, "side": "BUY",
         "entry_iv_pct": 7.0, "entry_price_per_contract_usd": 178.4},
        {"leg_idx": 1, "contract_type": "put", "tenor": "3M", "expiry": "2026-08-02", "dte": 90,
         "strike": 1.0850, "qty_factor": 1, "qty": 12, "side": "BUY",
         "entry_iv_pct": 7.0, "entry_price_per_contract_usd": 163.6},
    ]
    base = {
        "preview_id": f"tp_fixture_{name}",
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
        "signal_source": {
            "signal_id": -1, "pca_model_id": -1, "triggering_pc": 1,
            "z_score": -2.0, "label": "CHEAP",
        },
        "structure": {
            "type": "straddle_atm", "reference_tenor": "3M", "tenor_far": None,
            "requires_delta_hedge": True, "vega_sign": "positive",
            "legs": base_legs_straddle,
        },
        "greeks_net": {
            "vega_usd_per_volpt": 847.0, "gamma_usd_per_pip2": 2.3,
            "theta_usd_per_day": -89.0, "delta_unhedged": 0.05, "delta_post_hedge": 0.0,
        },
        "pricing": {
            "premium_paid_usd": 3420.0, "max_loss_usd": 3420.0,
            "max_loss_at_expiry_only": True, "breakeven_pips_each_side": 380,
        },
        "scenarios": [
            {"label": "favorable", "spot_move_pct": 2.0, "iv_reprice_volpts": 1.0,
             "pnl_gamma_theta_usd": 1200, "pnl_vega_usd": 847, "pnl_total_usd": 2047},
            {"label": "neutral", "spot_move_pct": 0.0, "iv_reprice_volpts": 0.0,
             "pnl_gamma_theta_usd": -800, "pnl_vega_usd": 0, "pnl_total_usd": -800},
            {"label": "adverse", "spot_move_pct": 0.5, "iv_reprice_volpts": -1.0,
             "pnl_gamma_theta_usd": -500, "pnl_vega_usd": -847, "pnl_total_usd": -1347},
        ],
        "sizing": {
            "base_qty": 10, "final_qty_per_leg": 12,
            "multipliers": {"z_score_factor": 1.33, "book_penalty": 0.9, "event_dampener": 1.0, "regime_multiplier": 1.0},
            "leg_quantities": {0: 12, 1: 12}, "final_premium_usd": 4104,
            "sizing_formula": "base × z_factor × book_penalty × event_dampener × regime_mult",
        },
        "pre_submit_checks": [
            {"name": "regime_not_pre_event", "passed": True, "details": {}},
            {"name": "signal_still_actionable", "passed": True, "details": {"current_z": -2.0, "armed_z": -2.0}},
            {"name": "max_loss_under_capital_limit", "passed": True, "details": {"max_loss_pct": 0.34, "limit_pct": 2.0}},
            {"name": "vega_under_book_limit", "passed": True, "details": {"post_trade_vega": 1247, "limit": 5000}},
            {"name": "iv_data_fresh", "passed": True, "details": {"data_age_seconds": 87, "limit": 120}},
            {"name": "no_arb_violation_on_legs", "passed": True, "details": {}},
            {"name": "minimum_liquidity", "passed": True, "details": {"min_quoted_size": 25, "limit": 10}},
        ],
        "state": "valid_for_submit",
        "blocking_reasons": [],
        "surface_age_seconds": 87.0,
    }
    if name == "valid_long_straddle":
        return base
    if name == "blocked_max_loss":
        base["pre_submit_checks"][2] = {
            "name": "max_loss_under_capital_limit", "passed": False,
            "details": {"max_loss_pct": 3.4, "limit_pct": 2.0},
        }
        base["state"] = "blocked"
        base["blocking_reasons"] = ["max_loss_under_capital_limit"]
        base["pricing"]["max_loss_usd"] = 6800.0
        return base
    if name == "blocked_signal_flipped":
        base["pre_submit_checks"][1] = {
            "name": "signal_still_actionable", "passed": False,
            "details": {"current_z": -0.8, "armed_z": -2.0},
        }
        base["state"] = "blocked"
        base["blocking_reasons"] = ["signal_still_actionable"]
        return base
    if name == "blocked_stale_data":
        base["pre_submit_checks"][4] = {
            "name": "iv_data_fresh", "passed": False,
            "details": {"data_age_seconds": 305, "limit": 120},
        }
        base["state"] = "blocked"
        base["blocking_reasons"] = ["iv_data_fresh"]
        base["surface_age_seconds"] = 305.0
        return base
    raise HTTPException(404, f"unknown fixture scenario: {name}")
