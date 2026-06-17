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
    actionable_check,
    check_coherence,
    classify_label,
    fit_pca_svd,
    is_persistent,
    pc3_sub_metrics,
    reason_category,
    sign_correct_loadings,
)
from persistence.models import (
    PcaModel,
    PcaSignal,
    SurfaceSnapshotHourly,
)

router = APIRouter(prefix="/api/v1", tags=["signals"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]

N_COMPONENTS = 6
# = N_COMPONENTS — strict mathematical minimum for SVD to extract 6 principal
# components. The Step 1 panel surfaces n_obs explicitly, so we don't gate
# Step 2 PCA when the fit is technically possible.
MIN_OBS_FOR_FIT = N_COMPONENTS


@router.get("/signals/pca/state")
async def state(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    scenario: str | None = Query(
        None, description="if set, returns the named PcaModel.version instead "
        "of the active one (fixture mode for UI testing)",
    ),
) -> dict[str, Any]:
    """Latest 3 signals (1 per PC) under the active PCA model — or, if
    ``?scenario=...`` is passed, under the model whose ``version`` matches
    the scenario tag (fixture mode)."""
    if scenario:
        model = (await db.execute(
            select(PcaModel).where(PcaModel.version == f"scenario_{scenario}").limit(1)
        )).scalar_one_or_none()
    else:
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
        # Active model exists but the vol-engine hasn't projected any cycle
        # yet (typical right after a refit, or with markets closed). Stay in
        # "stable" so the UI shows the active model + 3 empty PC cards rather
        # than the misleading "Pas de modèle PCA actif" bootstrap layout.
        var = list(model.variance_explained_ratio or [])
        return {
            "state": "stable", "model_version": model.version,
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
            "signals": {}, "coherence": {"all_coherent": True, "contradictions": []},
            "diagnostics": {"reason": "no_signals_yet"},
        }
    var = list(model.variance_explained_ratio or [])
    signals_payload = {
        f"pc{r.pc_id}": {
            "z_score": float(r.z_score), "raw_score": float(r.raw_score),
            "label": r.label, "actionable": r.actionable,
            "actionable_reason": r.actionable_reason,
            "reason_category": reason_category(r.actionable_reason),
            "sub_signals": r.sub_signals,
        } for r in rows
    }
    # Loadings reshaped to (n_pcs, 6 tenors, 5 deltas) for the UI heatmap.
    loadings_arr = np.asarray(model.loadings or [], dtype=float)
    loadings_grid = (
        loadings_arr.reshape(loadings_arr.shape[0], 6, 5).tolist()
        if loadings_arr.size and loadings_arr.shape[1] == N_FEATURES
        else []
    )
    return {
        "state": "stable",
        "timestamp": max(r.timestamp for r in rows),
        "model_version": model.version,
        "n_obs_in_fit": model.n_obs_used,
        "fit_timestamp": model.fit_timestamp,
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
        "loadings_grid": loadings_grid,        # (3, 6, 5) for heatmap
        "signals": signals_payload,
        "coherence": check_coherence(signals_payload),
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


async def perform_refit(db: AsyncSession, symbol: str = "EURUSD") -> dict[str, Any]:
    """Core refit logic — usable from the HTTP route AND the background scheduler.

    Reads surface_snapshots_hourly, fits PCA via SVD, sign-corrects vs prev
    active model, demotes prev, promotes new (in that order to honour the
    partial unique index ix_pca_models_active_unique).
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
        n_components_kept=N_COMPONENTS, is_active=False,  # flipped to True after demoting prev
        cosine_similarity_pc1=_finite_or_none(cos_sims[0] if len(cos_sims) > 0 else None),
        cosine_similarity_pc2=_finite_or_none(cos_sims[1] if len(cos_sims) > 1 else None),
        cosine_similarity_pc3=_finite_or_none(cos_sims[2] if len(cos_sims) > 2 else None),
        sign_flip_pc1=flips[0] if len(flips) > 0 else None,
        sign_flip_pc2=flips[1] if len(flips) > 1 else None,
        sign_flip_pc3=flips[2] if len(flips) > 2 else None,
    )
    db.add(new)
    await db.flush()
    # Demote prev before promoting new : the partial unique index
    # ix_pca_models_active_unique (WHERE is_active=true) forbids two active rows.
    if prev is not None:
        await db.execute(
            update(PcaModel).where(PcaModel.id == prev.id).values(
                is_active=False, superseded_by=new.id,
            )
        )
        await db.flush()
    await db.execute(
        update(PcaModel).where(PcaModel.id == new.id).values(is_active=True)
    )

    # Project the fit snapshots through the new loadings + persist as
    # pca_signals so the panel cards aren't empty after each refit.
    # Without this, every scheduler refit would orphan the previous
    # signals and the UI would flicker to "(no signal yet)" until the
    # vol-engine cycle catches up — which never happens with markets
    # closed (incomplete surface).
    await _backfill_signals_from_fit(
        db=db, model=new, X=X, snapshot_timestamps=[r.timestamp for r in rows
                                                     if all(getattr(r, c) is not None
                                                            for c in iv_cols)],
        symbol=symbol,
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


@router.post("/admin/pca/refit")
async def refit(db: DbDep, symbol: str = Query("EURUSD")) -> dict[str, Any]:
    """Manual refit (kept alongside the background scheduler so the user can
    force a refit after seeding new data or tweaking thresholds)."""
    return await perform_refit(db, symbol)


async def _backfill_signals_from_fit(
    *, db: AsyncSession, model: PcaModel, X: np.ndarray,
    snapshot_timestamps: list[datetime], symbol: str,
) -> None:
    """Project each row of the fit window X through the new loadings, compute
    z-scores per PC across the full history, and INSERT one PcaSignal per
    (snapshot, pc_id ∈ {1,2,3}) so the panel has data immediately.
    """
    means = np.asarray(model.means, dtype=float)
    stds = np.asarray(model.stds, dtype=float)
    loadings = np.asarray(model.loadings, dtype=float)
    var_ratio = list(model.variance_explained_ratio or [])
    cum_var = float(sum(var_ratio[:3])) if var_ratio else 0.0
    n_obs = int(model.n_obs_used)

    X_std = (X - means) / stds
    raw = X_std @ loadings.T  # (T, n_components)

    mu = raw.mean(axis=0)
    sigma = raw.std(axis=0, ddof=1)
    sigma = np.where(sigma <= 0, 1.0, sigma)
    z = (raw - mu) / sigma

    T = X.shape[0]
    rows_to_add: list[PcaSignal] = []
    for t in range(T):
        for pc_id in (1, 2, 3):
            idx = pc_id - 1
            z_t = float(z[t, idx])
            raw_t = float(raw[t, idx])
            label = classify_label(z_t)

            cos_sim = getattr(model, f"cosine_similarity_pc{pc_id}", None)
            stable = cos_sim is None or float(cos_sim) >= 0.85
            ve = float(var_ratio[idx]) if idx < len(var_ratio) else 0.0

            # persistence : last 3 z's most-recent-first
            z_history = [float(z[k, idx]) for k in range(t, max(-1, t - 3), -1)]
            persistent = is_persistent(z_history)

            flag = actionable_check(
                pc_id=pc_id, z_score=z_t, label=label,
                loadings_stable=stable, variance_explained=ve,
                persistent=persistent,
                n_obs=n_obs, cumulative_variance=cum_var,
            )
            sub = None
            if pc_id == 3:
                s, c = pc3_sub_metrics(X[t])
                # z over the column distribution
                skew_col = np.array([pc3_sub_metrics(X[k])[0] for k in range(T)])
                conv_col = np.array([pc3_sub_metrics(X[k])[1] for k in range(T)])
                skew_sigma = float(skew_col.std(ddof=1)) or 1.0
                conv_sigma = float(conv_col.std(ddof=1)) or 1.0
                sub = {
                    "skew_z": float((s - skew_col.mean()) / skew_sigma),
                    "convex_z": float((c - conv_col.mean()) / conv_sigma),
                }

            rows_to_add.append(PcaSignal(
                timestamp=snapshot_timestamps[t], symbol=symbol,
                pca_model_id=int(model.id), pc_id=pc_id,
                raw_score=raw_t, z_score=z_t, label=label,
                actionable=flag.actionable, actionable_reason=flag.reason,
                sub_signals=sub,
            ))
    db.add_all(rows_to_add)
    await db.flush()


def _finite_or_none(x: float | None) -> float | None:
    if x is None:
        return None
    if not np.isfinite(x):
        return None
    return float(x)
