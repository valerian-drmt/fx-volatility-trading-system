"""Step 3 — Trade preview API.

POST /api/v1/trade/preview              build full preview from a user-picked structure
POST /api/v1/trade/preview/{id}/cancel  user cancels a pending preview
GET  /api/v1/trade/preview/{id}         retrieve a stored preview
GET  /api/v1/trade/structures           list structure_definitions (catalogue)
GET  /api/v1/trade/limits               list current risk_limits
GET  /api/v1/trade/book                 current book state snapshot
"""
from __future__ import annotations

import logging
import os
import secrets
import traceback
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.auth import require_write
from api.dependencies import get_db_session, get_redis_client_or_none
from api.orchestration.book_state_refresh import refresh_book_state
from core.execution.revalidation import revalidate_preview
from core.execution.slippage import compute_limit_price
from core.products import product_label_from_symbol
from core.trade_preview import (
    TEMPLATES,
    _spot_from_surface,
    build_from_legs,
    build_structure,
    compute_legs_greeks,
    compute_net_greeks,
    compute_pnl_grid,
    compute_sizing,
    price_structure,
    run_pre_submit_checks,
    simulate_scenarios,
)
from core.trade_preview_regime import apply_regime_to_limits, regime_label
from persistence.models import (
    AppConfigScalar,  # replaces RiskLimit (migration 024)
    BookedPosition,
    BookStateSnapshot,
    IbConnectionState,
    PcaSignal,
    RegimeSnapshot,
    StructureFill,
    StructureOrder,
    TradeEvent,  # replaces ExecutionAuditLog (migration 025)
    TradePreviewRow,
    TradeStructure,
)
from shared.trace import current_trace_id

logger = logging.getLogger(__name__)

# Option legs go out as MARKETABLE LIMIT orders (fill instantly at the touch,
# behaviourally a market order) rather than raw MKT. A market order on an option
# hits IB's price-cap protection, which leaves BUY legs "Inactive" on wide spreads
# → naked half-fills. We cross the spread off the preview premium : BUY pays up
# (+buffer), SELL accepts down (−buffer). The buffer only guarantees the cross ;
# the fill is still at the NBBO touch. Futures stay MKT (deep, tight book).
_MARKETABLE_LIMIT_BUFFER = float(os.getenv("MARKETABLE_LIMIT_BUFFER", "0.25"))


def _scrub(value: object) -> str:
    """Neutralise CR/LF in user-supplied values before logging (CWE-117).

    Request-body fields (``preview_id``, ``execution_mode``, error details)
    flow into structured log lines; an embedded newline would let a caller
    forge fake log entries. Collapse CR/LF to spaces and cap the length.
    """
    return str(value).replace("\r", " ").replace("\n", " ")[:300]

router = APIRouter(prefix="/api/v1/trade", tags=["trade"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]


class LegSpec(BaseModel):
    """One free-composed leg (docs/strategy.md §4 — products/delta/tenor/side
    chosen freely, nothing imposed). Used by the ``legs`` preview path."""
    contract_type: Literal["call", "put", "future"]
    side: Literal["BUY", "SELL"]
    tenor: str                                       # e.g. "3M"
    delta_pillar: str | None = None                  # 10dp/25dp/atm/25dc/10dc (options)
    strike: float | None = None                      # explicit strike override (options)
    qty_factor: int = 1                              # relative weight; ×base_qty at sizing
    future_contract_size: Literal["full", "micro"] | None = None  # future legs only


class PreviewRequest(BaseModel):
    scenario: str | None = None             # fixture mode (returns canned preview)
    # Free composition (G-trade.preview): when set, the preview is built from
    # these legs directly — no template, no imposed structure. Takes precedence
    # over structure_type.
    legs: list[LegSpec] | None = None
    structure_type: str | None = None       # template path : user-picked structure
    tenor: str | None = None                # manual mode reference tenor
    tenor_far: str | None = None            # manual mode (calendar only)
    qty: int | None = None                  # manual mode base_qty
    delta_pillar: str | None = None         # single-leg only : override pillar
    strike_override: float | None = None    # single-leg only : override strike
    override_tenor: str | None = None
    override_far_tenor: str | None = None
    override_qty: int | None = None
    # CME EUR/USD future contract size : 'full' = 6E (€125 000) or
    # 'micro' = M6E (€12 500). Only used when structure_type is future_buy/sell.
    future_contract_size: Literal["full", "micro"] | None = None


async def _load_limits(db: AsyncSession) -> dict[str, float]:
    # risk_limits rows folded into config_scalar with namespace='risk'
    # (migration 024, table renamed 037). Same dict[name, value] return shape.
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
    """Read latest cached value of ``ib_connection_state.is_connected``.

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


async def _release_preview_lock(preview_id: str) -> None:
    """Drop the Redis lock held by ``_acquire_preview_lock``. Call in the
    error path so a failed submit doesn't block retries for the next 10 s.
    """
    client = get_redis_client_or_none()
    if client is None:
        return
    try:
        await client.delete(f"trade:submit_lock:{preview_id}")
    except Exception:
        pass


async def _post_execution_engine(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """HTTP POST to execution-engine on the internal Docker network.

    URL via ``EXECUTION_ENGINE_URL`` (default ``http://execution-engine:8001``).
    Failures bubble up as 502 — the structure rows are already persisted, so
    operator inspects + retries via ``POST /internal/structure/submit``.
    """
    import os

    import httpx

    from shared.trace import trace_headers
    base = os.environ.get("EXECUTION_ENGINE_URL", "http://execution-engine:8001")
    url = f"{base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=body, headers=trace_headers())
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
async def list_structures() -> list[dict[str, Any]]:
    """Catalogue of the 6 PCA-actionable structures. Source : the
    ``TEMPLATES`` dict in ``core.trade_preview`` (in_catalog=True
    entries). Was backed by ``structure_definition_ref`` until migration
    039 dropped that mirror table."""
    return [
        {
            "structure_type": k,
            "display_name": v["display"],
            "leg_template": v["legs"],
            "min_legs": len(v["legs"]),
            "max_legs": len(v["legs"]),
            "requires_delta_hedge": v["requires_delta_hedge"],
            "typical_vega_sign": v["vega_sign"],
            "typical_gamma_sign": v.get("typical_gamma_sign", "neutral"),
            "typical_theta_sign": v.get("typical_theta_sign", "neutral"),
            "rationale_for_pc": v.get("rationale_for_pc"),
        }
        for k, v in TEMPLATES.items()
        if v.get("in_catalog")
    ]


@router.get("/limits")
async def list_limits(db: DbDep) -> dict[str, dict[str, Any]]:
    # risk_limits rows folded into config_scalar with namespace='risk'
    # (migration 024, table renamed 037). Same response shape preserved.
    rows = (await db.execute(
        select(AppConfigScalar)
        .where(AppConfigScalar.namespace == "risk")
        .where(AppConfigScalar.is_active.is_(True))
    )).scalars().all()
    return {
        r.name: {"value": float(r.value), "unit": r.unit, "description": r.description}
        for r in rows
    }


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


@router.post("/preview", dependencies=[Depends(require_write)])
async def create_preview(req: PreviewRequest, db: DbDep, symbol: str = Query("EURUSD")) -> dict[str, Any]:
    """Build a full trade preview from a pca_signals.id. Persists to trade_previews
    and returns the payload conforming to STEP3 §4."""

    # Fixture mode : return a canned preview without touching live data.
    if req.scenario:
        return _fixture_preview(req.scenario)

    # The desk does not propose trades : the user always composes the position
    # directly — either free legs (`legs`, the general path) or a named template
    # (`structure_type`, kept as buildable reference). Signal-driven auto-
    # structuring was removed — the PCA engine no longer emits recommended_structure.
    manual_mode = True
    signal = None
    free_legs = req.legs is not None
    if not free_legs and req.structure_type is None:
        raise HTTPException(400, "legs or structure_type required (or use ?scenario=…)")

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

    # 3. Build the structure. Free-legs path = the trader composed arbitrary
    #    legs (products/delta/tenor/side); template path = a named reference.
    if free_legs:
        try:
            structure = build_from_legs([leg.model_dump() for leg in req.legs], surface)
        except ValueError as exc:
            raise HTTPException(400, f"invalid legs: {exc}") from exc
    else:
        near_tenor = (req.tenor or "3M").upper()
        far_tenor = req.tenor_far.upper() if req.tenor_far else None
        if req.override_tenor:
            near_tenor = req.override_tenor.upper()
        if req.override_far_tenor:
            far_tenor = req.override_far_tenor.upper()
        try:
            structure = build_structure(
                req.structure_type, near_tenor, far_tenor, surface,
                delta_pillar_override=req.delta_pillar,
                strike_override=req.strike_override,
                future_contract_size=req.future_contract_size,
            )
        except ValueError as exc:
            raise HTTPException(400, f"unknown structure: {exc}") from exc
    pricing = price_structure(structure, surface)
    greeks = compute_net_greeks(structure, surface)

    # Cosmetic display name = bare product family. Strips side prefix
    # (long_/short_), trailing pillar suffix (_atm/_25d/_10d) and the
    # future_buy/future_sell side marker. The side info is already on a
    # dedicated column in the post-trade tables ; the delta info lives on
    # each leg's strike. Keeps "vanilla_call" / "vanilla_put" as-is so
    # the contract-type (≠ side) is still visible.
    def _bare_product(s: str) -> str:
        # Strip side prefix.
        for pref in ("long_", "short_"):
            if s.startswith(pref):
                s = s[len(pref):]
                break
        # Strip pillar suffix.
        for suf in ("_atm", "_25d", "_10d"):
            if s.endswith(suf):
                s = s[: -len(suf)]
                break
        # Strip calendar side suffix.
        for suf in ("_long", "_short"):
            if s.endswith(suf):
                s = s[: -len(suf)]
                break
        # Futures collapse to single "future".
        if s in ("future_buy", "future_sell"):
            return "future"
        return s
    # Free-legs : the classifier label (e.g. "long strangle") is the display
    # name — vocabulary, not a template code. Templates : bare product family.
    display_structure_type = (
        (structure.product_label or "custom") if free_legs else _bare_product(structure.type)
    )

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
    legs_greeks_raw = compute_legs_greeks(structure, surface)
    pnl_grid = compute_pnl_grid(structure, surface, greeks)

    # Scale per-leg greeks to the actual trade size — same logic as
    # greeks_net above. ``qty_factor`` becomes the final per-leg quantity
    # (qty_factor × base_qty) so the operator sees "what's actually
    # being traded".
    _base_qty_for_scaling = max(sizing.base_qty, 1)
    legs_greeks: list[dict[str, Any]] = []
    for r in legs_greeks_raw:
        scaled = dict(r)
        scaled["qty_factor"] = int(r.get("qty_factor", 1)) * _base_qty_for_scaling
        for k in ("vega", "gamma", "theta", "delta"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                scaled[k] = v * _base_qty_for_scaling
        legs_greeks.append(scaled)

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
    # For futures we override the generic per-contract rate with the
    # CME-published rate for the chosen contract size (6E: $2.40, M6E: $0.60).
    if structure.future_contract_size:
        from core.trade_preview import FUTURE_COMMISSION_USD
        commission_per_contract = FUTURE_COMMISSION_USD[structure.future_contract_size]
    else:
        commission_per_contract = limits.get("commission_per_contract_usd", 2.0)
    total_contracts = sum(abs(q) for q in sizing.leg_quantities.values())
    commission_usd = float(total_contracts) * commission_per_contract
    # "Cost per contract" displayed on the RED block. Options : premium of
    # one structure unit (=total_premium / base_qty). Futures : no premium,
    # so we expose the commission per contract instead so the operator sees
    # a non-zero entry.
    if structure.future_contract_size:
        premium_per_contract_usd = commission_per_contract
    else:
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
            "type": display_structure_type,
            "type_template": structure.type,           # canonical template name
            "reference_tenor": structure.reference_tenor,
            "tenor_far": structure.tenor_far,
            "requires_delta_hedge": structure.requires_delta_hedge,
            "vega_sign": structure.vega_sign,
            "future_contract_size": structure.future_contract_size,
            # Trader-facing ticker (6E / M6E) — distinct from the IB API
            # Contract.symbol which is "EUR" for 6E. Cf. shared/contracts.py.
            "ib_symbol": (
                "6E" if structure.future_contract_size == "full"
                else "M6E" if structure.future_contract_size == "micro"
                else None
            ),
            "future_multiplier_eur": (
                125_000 if structure.future_contract_size == "full"
                else 12_500 if structure.future_contract_size == "micro"
                else None
            ),
            "legs": [
                {
                    "leg_idx": leg.leg_idx, "contract_type": leg.contract_type,
                    "tenor": leg.tenor, "expiry": leg.expiry, "dte": leg.dte,
                    "strike": leg.strike, "qty_factor": leg.qty_factor,
                    "qty": sizing.leg_quantities.get(leg.leg_idx, 0),
                    "side": leg.side, "entry_iv_pct": leg.entry_iv_pct,
                    # Snap (surface tenor change): an interpolated requested tenor
                    # trades the nearest LISTED tenor. requested_tenor / snapped
                    # let the ticket show "6M → trading 5M".
                    "requested_tenor": leg.requested_tenor,
                    "snapped": leg.snapped,
                    "entry_price_per_contract_usd": pricing.leg_prices_usd[leg.leg_idx]
                                                     if leg.leg_idx < len(pricing.leg_prices_usd) else 0.0,
                } for leg in structure.legs
            ],
        },
        # greeks scaled to the actual trade size (× base_qty). compute_net_greeks
        # returns per-structure-unit values; the operator-facing display
        # ought to reflect the full trade impact.
        "greeks_net": {
            "vega_usd_per_volpt": round(greeks.vega_usd_per_volpt * sizing.base_qty, 4),
            "gamma_usd_per_pip2": round(greeks.gamma_usd_per_pip2 * sizing.base_qty, 6),
            "theta_usd_per_day": round(greeks.theta_usd_per_day * sizing.base_qty, 4),
            "delta_unhedged": round(greeks.delta_unhedged * sizing.base_qty, 4),
            "delta_post_hedge": round(greeks.delta_post_hedge * sizing.base_qty, 4),
            "_per_unit": {  # raw per-structure-unit values (for debug / scaling)
                "vega_usd_per_volpt": greeks.vega_usd_per_volpt,
                "gamma_usd_per_pip2": greeks.gamma_usd_per_pip2,
                "theta_usd_per_day": greeks.theta_usd_per_day,
                "delta_unhedged": greeks.delta_unhedged,
                "delta_post_hedge": greeks.delta_post_hedge,
            },
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
        # Spot used for the pricing/greeks computation. Echoed back so the
        # frontend can render "Market data · Spot" without a separate
        # /vol/term-structure round-trip (which sometimes returns no
        # forward when the FX market is closed).
        "spot": _spot_from_surface(surface),
    }

    # 7. Persist (audit)
    row = TradePreviewRow(
        preview_id=preview_id, expires_at=expires_at,
        pca_signal_id=signal.id if signal else None,
        triggering_pc=signal.pc_id if signal else None,
        armed_z_score=float(signal.z_score) if signal else None,
        armed_signal_label=signal.label if signal else None,
        # Persist the display-friendly type so tables / audit show
        # "straddle_10d" instead of canonical "straddle_atm" when override.
        structure_type=display_structure_type,
        # Free-legs carry the classifier label directly (not in the template→label
        # map); templates resolve via the canonical helper.
        product_label=(
            structure.product_label if free_legs
            else product_label_from_symbol(None, display_structure_type)
        ),
        reference_tenor=structure.reference_tenor,
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
    # Display pillars (core.vol.tenors): 1M..6M.
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


@router.post("/submit", dependencies=[Depends(require_write)])
async def submit_preview(
    body: dict[str, Any], db: DbDep,
) -> dict[str, Any]:
    """Step 4 — Submit a previewed trade (mock or live).

    Wraps the real impl in a try/except so that unhandled Python exceptions
    surface as structured JSON instead of HTML "Internal Server Error".
    All errors also land in structlog with ``event='trade_submit_failed'``
    so Grafana / Loki can pick them up by ``{container="fxvol-api"} |= "trade_submit_failed"``.
    """
    preview_id = body.get("preview_id")
    execution_mode_arg = body.get("execution_mode", "mock")
    logger.info(
        "trade_submit_received preview_id=%s execution_mode=%s",
        _scrub(preview_id), _scrub(execution_mode_arg),
    )
    try:
        return await _submit_preview_impl(body, db)
    except HTTPException as he:
        # Release the lock so the user can retry without waiting 10 s for
        # the Redis TTL to expire. We DON'T release the lock on success —
        # the submission is recorded and the preview can't be re-used.
        if preview_id:
            await _release_preview_lock(str(preview_id))
        # Log structured for Grafana.
        logger.warning(
            "trade_submit_http_error preview_id=%s status=%s detail=%s",
            _scrub(preview_id), he.status_code, _scrub(he.detail),
        )
        raise
    except Exception as exc:
        if preview_id:
            await _release_preview_lock(str(preview_id))
        tb = traceback.format_exc()
        logger.error(
            "trade_submit_failed preview_id=%s exc_type=%s exc=%s\n%s",
            _scrub(preview_id), type(exc).__name__, str(exc)[:500], tb,
        )
        raise HTTPException(
            500,
            detail={
                "error": "trade_submit_unhandled_exception",
                "exception_type": type(exc).__name__,
                "message": str(exc)[:500],
                # Last 5 frames of the traceback for quick diagnosis on the UI.
                "traceback_tail": tb.strip().splitlines()[-5:],
            },
        ) from exc


async def _submit_preview_impl(
    body: dict[str, Any], db: DbDep,
) -> dict[str, Any]:
    """Actual submit impl. See ``submit_preview`` for the wrapper rationale.

    Loads preview, runs revalidation, creates trade_structure + trade_order
    rows, dispatches to execution-engine for live mode or synthesises mock
    fills for mock mode.
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
        db.add(TradeEvent(
            structure_id=None, event_type="submission_blocked",
            severity="warning", description="ib_disconnected_at_submit",
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
        db.add(TradeEvent(
            structure_id=None, event_type="submission_blocked",
            severity="warning", description=f"revalidation_failed: {revalidation.reason}",
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
    trace_id = current_trace_id()  # correlation id of this Submit request
    structure = TradeStructure(
        preview_id=preview_id,
        pca_signal_id=preview.pca_signal_id,
        triggering_pc=preview.triggering_pc,
        armed_z_score=preview.armed_z_score,
        armed_signal_label=preview.armed_signal_label,
        structure_type=preview.structure_type,
        product_label=product_label_from_symbol(None, preview.structure_type),
        reference_tenor=preview.reference_tenor,
        expiry_date=expiry_d,
        base_qty=base_qty,
        state="submitted",
        execution_mode=execution_mode,
        trace_id=trace_id,
    )
    db.add(structure)
    await db.flush()

    db.add(TradeEvent(
        structure_id=structure.id, event_type="submission_attempt",
        severity="info",
        description=f"{execution_mode} submit for preview {preview_id}",
    ))

    now = datetime.now(UTC)

    # ── LIVE PATH ──────────────────────────────────────────────────────
    # Persist orders in 'pending' state, then call execution-engine which
    # places them via ib_insync and wires fills handlers. The cascade to
    # state='filled' / 'fully_filled' / trade_positions arrives via events.
    if execution_mode == "live":
        # For futures, route the order to the right CME ticker — 6E for
        # 'full' size, M6E for 'micro'. Options keep the legacy default
        # 'EUR' (which the contract_builder maps to the FOP trading class).
        # ``future_contract_size`` lives on the preview's frozen payload
        # (the JSONB ``structure_full_payload`` column) — we never persisted
        # it as a TradeStructure column so we read it back here.
        from core.trade_preview import FUTURE_IB_SYMBOLS as _FIB
        fcs_from_payload = (payload.get("structure") or {}).get("future_contract_size")
        fut_symbol = _FIB.get(fcs_from_payload or "")
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
            # Live limit price for futures with preview_price=0 is meaningless ;
            # use the spot from the surface as a sensible starting LMT.
            # Future contract_symbol gets the CME ticker (6E / M6E).
            extra_kwargs: dict[str, Any] = {}
            if contract_type == "future":
                # IB API symbol : "EUR" for 6E full, "M6E" for micro. Default
                # to "EUR" (the most common case).
                extra_kwargs["contract_symbol"] = fut_symbol or "EUR"
                # No live Redis surface here — read the spot that was
                # echoed back in the preview payload (added to the response
                # at preview time, lives in structure_full_payload.spot).
                if preview_price <= 0:
                    spot_from_preview = payload.get("spot")
                    if isinstance(spot_from_preview, (int, float)) and spot_from_preview > 0:
                        preview_price = float(spot_from_preview)
                    else:
                        preview_price = 1.0  # last-resort to keep flow alive
            # Futures : MKT. Options : MARKETABLE LIMIT crossing the spread from the
            # preview premium so the BUY legs don't die on IB's option price-cap
            # (→ naked half-fills). ``preview_price`` is already the premium in price
            # points (CONTRACT_MULTIPLIER=1) — exactly IB's lmtPrice unit, no scaling.
            # Falls back to MKT if there's no premium (or it rounds to zero).
            if contract_type == "future" or not (preview_price and preview_price > 0):
                order_type_db = "MKT"
                limit_price = None
            else:
                buf = _MARKETABLE_LIMIT_BUFFER
                # Snap to the CME EUR-FOP price tick (0.0001) or IB rejects with
                # error 110 "price does not conform to minimum price variation"
                # and the order sticks at PendingSubmit. Round to 4 dp (a 0.0001
                # multiple is valid on both the 0.0001 and 0.00005 grids).
                lp = round(
                    preview_price * (1 + buf) if side == "BUY" else preview_price * (1 - buf),
                    4,
                )
                if lp > 0:
                    order_type_db = "LMT"
                    limit_price = lp
                else:
                    order_type_db = "MKT"
                    limit_price = None
            db.add(StructureOrder(
                structure_id=structure.id, leg_idx=i, order_role="entry",
                contract_type=contract_type, contract_expiry=contract_expiry,
                contract_strike=float(contract_strike)
                                 if isinstance(contract_strike, (int, float)) else None,
                side=side, qty=qty,
                order_type=order_type_db, limit_price=limit_price,
                preview_iv_pct=leg.get("entry_iv_pct"),
                preview_price=preview_price,
                state="pending",
                trace_id=trace_id,
                **extra_kwargs,
            ))
        preview.user_action = "submitted"
        preview.user_action_at = now
        preview.state = "submitted"
        await db.commit()
        logger.info(
            "trade_submit_persisted_live structure_id=%s n_legs=%s preview_id=%s",
            structure.id, len(legs), _scrub(preview_id),
        )

        # Fire-and-forget HTTP call to execution-engine. Failure here does
        # not roll the structure back automatically — operator decides.
        logger.info(
            "trade_submit_dispatch_ee structure_id=%s url=/internal/structure/submit",
            structure.id,
        )
        ee_result = await _post_execution_engine(
            "/internal/structure/submit",
            {"structure_id": structure.id},
        )
        logger.info(
            "trade_submit_ee_ok structure_id=%s body=%s",
            structure.id, str(ee_result)[:300],
        )
        return {
            "success": True,
            "structure_id": structure.id,
            "position_id": None,                   # arrives via fill cascade
            "n_orders_submitted": len(legs),
            "execution_mode": "live",
            "state": "submitted",
            "trace_id": current_trace_id(),        # trace this submit across services
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
            trace_id=trace_id,
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
    position = BookedPosition(
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

    db.add(TradeEvent(
        structure_id=structure.id, event_type="structure_filled",
        severity="info", description="mock fully_filled, position created",
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
            select(BookedPosition).where(BookedPosition.structure_id == s.id).limit(1)
        )).scalar_one_or_none()
        # Order role = the desk's real taxonomy (entry / closing / unwind / hedge).
        # A closing structure (from a position close) carries role 'closing', an
        # opener 'entry' — so the blotter can label open vs close correctly.
        order_role = (await db.execute(
            select(StructureOrder.order_role).where(StructureOrder.structure_id == s.id).limit(1)
        )).scalar_one_or_none()
        # For a closing structure, the ORIGINAL trade it closes: the closing order's
        # closes_order_id -> the entry order -> its structure_id. Lets the blotter
        # show "#30" (the trade being closed), not "#31" (this new closing
        # structure). NULL when the close wasn't linked to an entry leg.
        closes_trade_id = None
        if order_role in ("closing", "unwind"):
            _entry = aliased(StructureOrder)
            closes_trade_id = (await db.execute(
                select(_entry.structure_id)
                .select_from(StructureOrder)
                .join(_entry, StructureOrder.closes_order_id == _entry.id)
                .where(StructureOrder.structure_id == s.id)
                .limit(1)
            )).scalar_one_or_none()
        # Contract(s) traded : the IB localSymbol(s) of the structure's legs. One
        # symbol for a single-leg order (a close, a vanilla) ; "sym +N" for a
        # multi-leg structure (butterfly / strangle). None until the first fill
        # stamps ib_local_symbol.
        leg_syms = (await db.execute(
            select(StructureOrder.ib_local_symbol)
            .where(StructureOrder.structure_id == s.id, StructureOrder.ib_local_symbol.is_not(None))
            .order_by(StructureOrder.leg_idx)
        )).scalars().all()
        uniq_syms = list(dict.fromkeys(leg_syms))  # distinct, order-preserving
        contract = (
            None if not uniq_syms
            else uniq_syms[0] if len(uniq_syms) == 1
            else f"{uniq_syms[0]} +{len(uniq_syms) - 1}"
        )
        out.append({
            "id": s.id, "created_at": s.created_at, "structure_type": s.structure_type,
            "product_label": s.product_label, "contract": contract,
            "reference_tenor": s.reference_tenor, "base_qty": s.base_qty, "state": s.state,
            "execution_mode": s.execution_mode,
            "order_role": order_role or "entry",
            "closes_trade_id": closes_trade_id,
            "total_premium_paid_usd": s.total_premium_paid_usd,
            "total_commission_usd": s.total_commission_usd,
            "total_entry_cost_usd": s.total_entry_cost_usd,
            "preview_id": s.preview_id,
            "position_id": position.id if position else None,
            "position_state": position.state if position else None,
        })
    return out


@router.post("/preview/{preview_id}/cancel", dependencies=[Depends(require_write)])
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
