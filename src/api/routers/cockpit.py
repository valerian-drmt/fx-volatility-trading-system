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

from api.auth import require_write
from api.dependencies import get_db_session, get_redis
from api.orchestration import vol_service

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


@router.post("/trade-preview", response_model=TradePreviewResponse, dependencies=[Depends(require_write)])
async def trade_preview(
    body: TradePreviewRequest,
    redis: RedisDep,
) -> TradePreviewResponse:
    from engines.execution import structures as S

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
    svi_params_count: int
    last_vol_surface_ts: datetime | None
    pca_ready: bool


@router.get("/model-health", response_model=ModelHealthResponse)
async def model_health(db: DbDep) -> ModelHealthResponse:
    from core.vol.surface_pca import MIN_SAMPLES_FOR_SIGNAL as PCA_MIN
    from persistence.models import VolSurface

    vs_count = int((await db.execute(
        select(VolSurface).with_only_columns(VolSurface.id).order_by(None)
    )).scalars().all().__len__())
    # SVI fits 1:1 with vol_surface_history rows (params live in surface_data._svi).
    svi_count = vs_count
    last_vs = (await db.execute(
        select(VolSurface).order_by(desc(VolSurface.timestamp)).limit(1)
    )).scalar_one_or_none()
    return ModelHealthResponse(
        vol_surfaces_count=vs_count,
        svi_params_count=svi_count,
        last_vol_surface_ts=last_vs.timestamp if last_vs else None,
        pca_ready=vs_count >= PCA_MIN,
    )
