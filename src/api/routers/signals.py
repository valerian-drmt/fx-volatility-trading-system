"""Step 2 — PCA signals API.

GET  /api/v1/signals/pca/state              latest 3-PC signal payload
GET  /api/v1/signals/pca/history?n=N        N latest signals (any PC)
GET  /api/v1/signals/pca/model              active PCA model meta
POST /api/v1/admin/pca/refit                trigger PCA refit (manual MVP)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session
from core.vol.pca_engine import (
    N_FEATURES,
    fit_pca_svd,
    sign_correct_loadings,
)
from persistence.models import (
    PcaModel,
    PcaSignal,
    SurfaceSnapshotHourly,
)

router = APIRouter(prefix="/api/v1", tags=["signals"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]

MIN_OBS_FOR_FIT = 30  # MVP : keep low so first refit possible after ~30h
N_COMPONENTS = 6


@router.get("/signals/pca/state")
async def state(
    db: DbDep, symbol: str = Query("EURUSD", min_length=3, max_length=20),
) -> dict[str, Any]:
    """Latest 3 signals (1 per PC) under the active PCA model."""
    model = (await db.execute(
        select(PcaModel).where(PcaModel.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if model is None:
        return {"state": "bootstrap", "model_version": None, "signals": {}}

    # Latest signal per PC for this symbol + active model.
    rows: list[PcaSignal] = []
    for pc_id in (1, 2, 3):
        row = (await db.execute(
            select(PcaSignal)
            .where(PcaSignal.symbol == symbol)
            .where(PcaSignal.pca_model_id == model.id)
            .where(PcaSignal.pc_id == pc_id)
            .order_by(desc(PcaSignal.timestamp))
            .limit(1)
        )).scalar_one_or_none()
        if row is not None:
            rows.append(row)

    if not rows:
        return {
            "state": "bootstrap", "model_version": model.version,
            "signals": {}, "diagnostics": {"reason": "no_signals_yet"},
        }
    var = list(model.variance_explained_ratio or [])
    return {
        "state": "stable",
        "timestamp": max(r.timestamp for r in rows),
        "model_version": model.version,
        "n_obs_in_fit": model.n_obs_used,
        "fit_window_start": model.fit_window_start,
        "fit_window_end": model.fit_window_end,
        "variance_explained": {
            "pc1": float(var[0]) if len(var) > 0 else 0.0,
            "pc2": float(var[1]) if len(var) > 1 else 0.0,
            "pc3": float(var[2]) if len(var) > 2 else 0.0,
            "cumulative": float(sum(var[:3])) if var else 0.0,
        },
        "loadings_stable": {
            f"pc{i}": (
                getattr(model, f"cosine_similarity_pc{i}", None) is None
                or float(getattr(model, f"cosine_similarity_pc{i}")) >= 0.85
            ) for i in (1, 2, 3)
        },
        "signals": {
            f"pc{r.pc_id}": {
                "z_score": float(r.z_score), "raw_score": float(r.raw_score),
                "label": r.label, "actionable": r.actionable,
                "actionable_reason": r.actionable_reason,
                "recommended_structure": r.recommended_structure,
            } for r in rows
        },
    }


@router.get("/signals/pca/history")
async def history(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    pc_id: int = Query(1, ge=1, le=6),
    n: int = Query(90, ge=1, le=500),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(PcaSignal)
        .where(PcaSignal.symbol == symbol)
        .where(PcaSignal.pc_id == pc_id)
        .order_by(desc(PcaSignal.timestamp))
        .limit(n)
    )).scalars().all()
    return [
        {
            "timestamp": r.timestamp, "z_score": float(r.z_score),
            "label": r.label, "actionable": r.actionable,
        } for r in rows
    ]


@router.get("/signals/pca/model")
async def active_model(db: DbDep) -> dict[str, Any]:
    model = (await db.execute(
        select(PcaModel).where(PcaModel.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    snap_count_row = (await db.execute(
        select(SurfaceSnapshotHourly.id)
    )).scalars().all()
    n_snaps_total = len(snap_count_row)
    return {
        "active": model is not None,
        "version": model.version if model else None,
        "n_obs_used": model.n_obs_used if model else None,
        "fit_window": {
            "start": model.fit_window_start if model else None,
            "end": model.fit_window_end if model else None,
        } if model else None,
        "variance_explained": list(model.variance_explained_ratio) if model else None,
        "available_hourly_snapshots": n_snaps_total,
        "min_obs_for_refit": MIN_OBS_FOR_FIT,
        "ready_to_refit": n_snaps_total >= MIN_OBS_FOR_FIT,
    }


@router.post("/admin/pca/refit")
async def refit(db: DbDep, symbol: str = Query("EURUSD")) -> dict[str, Any]:
    """Refit PCA on the available surface_snapshots_hourly (MVP : manual trigger).

    In production this would be a cron job in `pca-fitter` container ; for the
    MVP we expose it as an admin endpoint so it can be called manually once
    enough hourly snapshots have accumulated.
    """
    rows = (await db.execute(
        select(SurfaceSnapshotHourly)
        .where(SurfaceSnapshotHourly.symbol == symbol)
        .order_by(SurfaceSnapshotHourly.timestamp)
    )).scalars().all()
    if len(rows) < MIN_OBS_FOR_FIT:
        raise HTTPException(
            400, f"need ≥ {MIN_OBS_FOR_FIT} snapshots to fit, have {len(rows)}"
        )

    iv_cols = [f"iv_{t}_{d}" for t in ("1m","2m","3m","4m","5m","6m")
               for d in ("10dp","25dp","atm","25dc","10dc")]
    X_list: list[list[float]] = []
    for r in rows:
        vec = [getattr(r, c) for c in iv_cols]
        if any(v is None for v in vec):
            continue
        X_list.append([float(v) for v in vec])
    if len(X_list) < MIN_OBS_FOR_FIT:
        raise HTTPException(
            400, f"only {len(X_list)} clean snapshots after filtering null IVs",
        )
    X = np.asarray(X_list, dtype=float)
    if X.shape[1] != N_FEATURES:
        raise HTTPException(500, f"shape mismatch: got {X.shape}, expected (T,{N_FEATURES})")

    # Sign-correct vs previous active model loadings.
    prev = (await db.execute(
        select(PcaModel).where(PcaModel.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    prev_loadings = np.asarray(prev.loadings, dtype=float) if prev else None

    fit = fit_pca_svd(X, n_components=N_COMPONENTS)
    corrected, cos_sims, flips = sign_correct_loadings(fit.loadings, prev_loadings)

    now = datetime.now(UTC)
    version = f"pca_v1_{now.strftime('%Y_%m_%d_%H%M%S')}"
    new = PcaModel(
        version=version,
        fit_window_start=rows[0].timestamp,
        fit_window_end=rows[-1].timestamp,
        n_obs_used=int(fit.n_obs_used),
        means=fit.means.tolist(), stds=fit.stds.tolist(),
        loadings=corrected.tolist(),
        eigenvalues=fit.eigenvalues.tolist(),
        variance_explained_ratio=fit.variance_explained_ratio.tolist(),
        n_components_kept=N_COMPONENTS, is_active=True,
        cosine_similarity_pc1=_finite_or_none(cos_sims[0] if len(cos_sims) > 0 else None),
        cosine_similarity_pc2=_finite_or_none(cos_sims[1] if len(cos_sims) > 1 else None),
        cosine_similarity_pc3=_finite_or_none(cos_sims[2] if len(cos_sims) > 2 else None),
        sign_flip_pc1=flips[0] if len(flips) > 0 else None,
        sign_flip_pc2=flips[1] if len(flips) > 1 else None,
        sign_flip_pc3=flips[2] if len(flips) > 2 else None,
    )
    db.add(new)
    await db.flush()
    if prev is not None:
        await db.execute(
            update(PcaModel).where(PcaModel.id == prev.id).values(
                is_active=False, superseded_by=new.id,
            )
        )
    await db.commit()
    await db.refresh(new)
    return {
        "version": new.version, "n_obs_used": new.n_obs_used,
        "variance_explained_ratio": list(new.variance_explained_ratio)[:3],
        "cosine_similarity": [
            float(new.cosine_similarity_pc1) if new.cosine_similarity_pc1 is not None else None,
            float(new.cosine_similarity_pc2) if new.cosine_similarity_pc2 is not None else None,
            float(new.cosine_similarity_pc3) if new.cosine_similarity_pc3 is not None else None,
        ],
        "previous_version": prev.version if prev else None,
    }


def _finite_or_none(x: float | None) -> float | None:
    if x is None:
        return None
    if not np.isfinite(x):
        return None
    return float(x)
