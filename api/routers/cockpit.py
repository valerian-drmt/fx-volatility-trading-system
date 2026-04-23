"""Cockpit endpoints — backs the 6 frontend panels (refactor plan P6).

Each endpoint has a ``bootstrap`` flag that fires true until the
underlying historical series has accumulated enough observations to
emit a trustworthy signal. The React cockpit uses that flag to render
"accumulating" placeholders instead of false positives.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from redis import asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from api.services import vol_service

router = APIRouter(prefix="/api/v1/vol", tags=["cockpit"])

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db_session)]


# ────────────────────────────────────────────────────────────────
# P6.1 — Regime Detector
# ────────────────────────────────────────────────────────────────


class RegimeResponse(BaseModel):
    regime: str
    probabilities: dict[str, float]
    features: dict[str, float | None]
    vrp_by_tenor: dict[str, float]
    event_dampener: bool
    bootstrap: bool


@router.get("/regime", response_model=RegimeResponse)
async def regime(
    redis: RedisDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
) -> RegimeResponse:
    """Current market regime + per-tenor expected VRP."""
    from core.vol.vrp import VRP_DEFAULTS_VOL_PTS, detect_regime

    try:
        surface = await vol_service.get_latest_surface(redis, symbol)
    except vol_service.VolNotFound:
        return RegimeResponse(
            regime="calm", probabilities={"calm": 1.0, "stressed": 0.0, "pre_event": 0.0},
            features={"vol_level": None, "vol_of_vol": None, "term_slope": None},
            vrp_by_tenor=VRP_DEFAULTS_VOL_PTS["calm"],
            event_dampener=False, bootstrap=True,
        )
    rv = surface.surface.get("_rv_full_pct")
    atm_1m = ((surface.surface.get("1M") or {}).get("atm") or {}).get("iv")
    atm_6m = ((surface.surface.get("6M") or {}).get("atm") or {}).get("iv")
    slope = None
    if isinstance(atm_1m, (int, float)) and isinstance(atm_6m, (int, float)):
        slope = (float(atm_6m) - float(atm_1m)) * 100.0
    regime_label = detect_regime(
        vol_level_pct=float(rv) if isinstance(rv, (int, float)) else None,
        vol_of_vol_pct=None,
        term_slope_pct=slope,
    )
    # Simple one-hot probabilities until the GMM lands — honest rather than faked.
    probs = {"calm": 0.0, "stressed": 0.0, "pre_event": 0.0}
    probs[regime_label] = 1.0
    return RegimeResponse(
        regime=regime_label,
        probabilities=probs,
        features={
            "vol_level": float(rv) if isinstance(rv, (int, float)) else None,
            "vol_of_vol": None,
            "term_slope": slope,
        },
        vrp_by_tenor=VRP_DEFAULTS_VOL_PTS.get(regime_label, VRP_DEFAULTS_VOL_PTS["calm"]),
        event_dampener=False,
        bootstrap=True,   # GMM calibration not live yet
    )


# ────────────────────────────────────────────────────────────────
# P6.2 — PCA Signal Dashboard
# ────────────────────────────────────────────────────────────────


class PcSignalItem(BaseModel):
    pc: int
    label: str
    z_score: float
    current: float
    mean: float
    std: float
    bootstrap: bool
    recommended_structure: str | None
    recommended_tenor: str | None


class PcaSignalsResponse(BaseModel):
    timestamp: datetime | None
    signals: list[PcSignalItem]
    explained_variance: list[float]
    n_samples_trained: int
    bootstrap: bool


@router.get("/pca-signals", response_model=PcaSignalsResponse)
async def pca_signals(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    lookback_hours: int = Query(24, ge=1, le=720),
) -> PcaSignalsResponse:
    """Fit PCA on recent vol_surfaces and project the latest snapshot.

    When fewer than 50 surfaces are available in the lookback window,
    returns a bootstrap response with z-scores of 0 so the dashboard
    renders 'accumulating history' rather than a spurious alarm.
    """
    from core.vol.surface_pca import (
        compute_pc_signals,
        fit_pca,
        label_pc,
        project_surface,
    )
    from persistence.models import VolSurface

    stmt = (
        select(VolSurface).where(VolSurface.underlying == symbol)
        .order_by(desc(VolSurface.timestamp)).limit(500)
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return PcaSignalsResponse(
            timestamp=None, signals=[], explained_variance=[],
            n_samples_trained=0, bootstrap=True,
        )
    surfaces = [r.surface_data for r in rows if isinstance(r.surface_data, dict)]
    model = fit_pca(surfaces[-500:], n_components=3)
    if model is None:
        return PcaSignalsResponse(
            timestamp=rows[0].timestamp, signals=[], explained_variance=[],
            n_samples_trained=0, bootstrap=True,
        )
    current_surface = surfaces[0]
    current_scores = project_surface(current_surface, model)
    hist_scores = [project_surface(s, model) for s in surfaces[1:]]
    sigs = compute_pc_signals(current_scores, hist_scores, model)
    labels = label_pc(model)

    items: list[PcSignalItem] = []
    for s in sigs:
        reco_structure = _structure_name_for(labels[s.pc - 1] if s.pc - 1 < len(labels) else "other")
        items.append(PcSignalItem(
            pc=s.pc, label=s.label, z_score=s.z, current=s.current,
            mean=s.mean, std=s.std, bootstrap=s.bootstrap,
            recommended_structure=reco_structure,
            recommended_tenor="3M" if reco_structure else None,
        ))
    return PcaSignalsResponse(
        timestamp=rows[0].timestamp, signals=items,
        explained_variance=[float(v) for v in model.explained_variance_ratio],
        n_samples_trained=model.n_samples_trained,
        bootstrap=model.bootstrap,
    )


def _structure_name_for(label: str) -> str | None:
    return {
        "level": "StraddleATM",
        "term_slope": "CalendarSpread",
        "smile": "Butterfly25d",
        "skew": "RiskReversal25d",
    }.get(label)


# ────────────────────────────────────────────────────────────────
# P6.3 — Trade Preview
# ────────────────────────────────────────────────────────────────


class TradePreviewRequest(BaseModel):
    structure: str       # 'StraddleATM' | 'RiskReversal25d' | 'Butterfly25d' | 'CalendarSpread'
    tenor: str           # '1M' .. '6M'
    side: str = "BUY"
    qty: int = 10
    tenor_far: str | None = None  # required for CalendarSpread


class LegItem(BaseModel):
    instrument: str
    side: str
    qty: int
    strike: float | None
    tenor: str
    iv: float | None
    premium_per_contract: float


class TradePreviewResponse(BaseModel):
    structure: str
    legs: list[LegItem]
    net_vega: float
    net_gamma: float
    net_theta: float
    net_delta: float
    total_premium: float
    bootstrap: bool


@router.post("/trade-preview", response_model=TradePreviewResponse)
async def trade_preview(
    body: TradePreviewRequest,
    redis: RedisDep,
) -> TradePreviewResponse:
    from services.execution import structures as S

    try:
        surface = await vol_service.get_latest_surface(redis, "EURUSD")
    except vol_service.VolNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    F_candidate = (surface.surface.get(body.tenor) or {}).get("atm", {}).get("strike")
    forward = float(F_candidate) if isinstance(F_candidate, (int, float)) else 1.17

    if body.structure == "StraddleATM":
        s = S.StraddleATM(tenor=body.tenor, side=body.side, qty=body.qty)
    elif body.structure == "RiskReversal25d":
        direction = "LONG_CALL" if body.side == "BUY" else "LONG_PUT"
        s = S.RiskReversal25d(tenor=body.tenor, direction=direction, qty=body.qty)
    elif body.structure == "Butterfly25d":
        s = S.Butterfly25d(tenor=body.tenor, side=body.side, qty=body.qty)
    elif body.structure == "CalendarSpread":
        if not body.tenor_far:
            raise HTTPException(status_code=400, detail="tenor_far required")
        s = S.CalendarSpread(
            tenor_near=body.tenor, tenor_far=body.tenor_far,
            side=body.side, qty=body.qty,
        )
    else:
        raise HTTPException(status_code=400, detail=f"unknown structure {body.structure!r}")

    legs = s.legs(forward, surface.surface)
    net = s.net_greeks(forward, surface.surface)
    if not legs:
        return TradePreviewResponse(
            structure=body.structure, legs=[], net_vega=0.0, net_gamma=0.0,
            net_theta=0.0, net_delta=0.0, total_premium=0.0, bootstrap=True,
        )
    signed_premium = sum(
        (leg.premium_per_contract * leg.qty * (-1 if leg.side == "BUY" else 1))
        for leg in legs if leg.instrument != "FUT"
    )
    return TradePreviewResponse(
        structure=body.structure,
        legs=[LegItem(
            instrument=leg.instrument, side=leg.side, qty=leg.qty, strike=leg.strike,
            tenor=leg.tenor, iv=leg.iv, premium_per_contract=leg.premium_per_contract,
        ) for leg in legs],
        net_vega=net.vega, net_gamma=net.gamma, net_theta=net.theta, net_delta=net.delta,
        total_premium=float(signed_premium),
        bootstrap=False,
    )


# ────────────────────────────────────────────────────────────────
# P6.6 — Model Health (lightweight version)
# ────────────────────────────────────────────────────────────────


class ModelHealthResponse(BaseModel):
    vol_surfaces_count: int
    signals_count: int
    svi_params_count: int
    last_vol_surface_ts: datetime | None
    last_signal_ts: datetime | None
    pca_ready: bool
    vrp_calibration_ready: bool
    fair_smile_ready: bool


@router.get("/model-health", response_model=ModelHealthResponse)
async def model_health(db: DbDep) -> ModelHealthResponse:
    from core.vol.calibration import (
        MIN_OBSERVATIONS_VRP_PER_TENOR,
        MIN_OBSERVATIONS_W1,
    )
    from core.vol.fair_smile import MIN_HISTORY_FOR_SIGNAL as FAIR_MIN
    from core.vol.surface_pca import MIN_SAMPLES_FOR_SIGNAL as PCA_MIN
    from persistence.models import Signal, SviParam, VolSurface

    vs_count = int((await db.execute(
        select(VolSurface).with_only_columns(VolSurface.id).order_by(None)
    )).scalars().all().__len__())
    sig_count = int((await db.execute(
        select(Signal).with_only_columns(Signal.id).order_by(None)
    )).scalars().all().__len__())
    svi_count = int((await db.execute(
        select(SviParam).with_only_columns(SviParam.id).order_by(None)
    )).scalars().all().__len__())

    last_vs = (await db.execute(
        select(VolSurface).order_by(desc(VolSurface.timestamp)).limit(1)
    )).scalar_one_or_none()
    last_sig = (await db.execute(
        select(Signal).order_by(desc(Signal.timestamp)).limit(1)
    )).scalar_one_or_none()
    _ = FAIR_MIN  # used in readiness check below
    _ = MIN_OBSERVATIONS_W1  # silence unused — kept for future readiness predicate
    return ModelHealthResponse(
        vol_surfaces_count=vs_count,
        signals_count=sig_count,
        svi_params_count=svi_count,
        last_vol_surface_ts=last_vs.timestamp if last_vs else None,
        last_signal_ts=last_sig.timestamp if last_sig else None,
        pca_ready=vs_count >= PCA_MIN,
        vrp_calibration_ready=sig_count >= 6 * MIN_OBSERVATIONS_VRP_PER_TENOR,
        fair_smile_ready=svi_count >= 6 * FAIR_MIN,
    )
