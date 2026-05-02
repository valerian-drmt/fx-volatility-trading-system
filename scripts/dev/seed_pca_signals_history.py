"""Project the seeded ``surface_snapshots_hourly`` rows through the active
PCA model and persist the resulting raw_score / z_score / label / actionable
into ``pca_signals``, so the Step 2 panel shows real PC1/PC2/PC3 cards
instead of "(no signal yet)" while markets are closed.

Usage (from project root, secrets loaded) :

    python scripts/dev/seed_pca_signals_history.py
    python scripts/dev/seed_pca_signals_history.py --shock-pc1 2.5
    python scripts/dev/seed_pca_signals_history.py --purge

Pipeline (mirrors ``engines/vol/engine.py:_compute_pca_signals``) :
  1. Read active ``pca_models`` row.
  2. Read every ``surface_snapshots_hourly`` for ``--symbol``, build the 30-dim
     vector (already in % vol).
  3. Standardise via the model's stored ``means`` / ``stds``.
  4. Project on ``loadings`` → raw_scores per PC.
  5. Compute z = (raw - μ) / σ across the full history per PC.
  6. Optionally apply ``--shock-pc1`` to the last 3 cycles so the panel shows
     a persistent actionable signal (label CHEAP if shock < 0, EXPENSIVE if > 0).
  7. Run the same actionability gates as the engine (variance, stability,
     magnitude, persistence) → INSERT 3 rows per cycle.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import delete, select  # noqa: E402

from core.vol.pca_engine import (  # noqa: E402
    DELTAS,
    TENORS,
    actionable_check,
    classify_label,
    is_persistent,
)
from persistence.db import get_sessionmaker  # noqa: E402
from persistence.models import (  # noqa: E402
    PcaModel,
    PcaSignal,
    SignalRecommendationsMap,
    SurfaceSnapshotHourly,
)


def _project_history(
    snapshots: list[SurfaceSnapshotHourly],
    means: np.ndarray, stds: np.ndarray, loadings: np.ndarray,
) -> np.ndarray:
    """Return raw_scores matrix (T, n_components)."""
    T = len(snapshots)
    n_comp = loadings.shape[0]
    out = np.zeros((T, n_comp))
    for t, snap in enumerate(snapshots):
        x = np.array([
            float(getattr(snap, f"iv_{ten.lower()}_{d}"))
            for ten in TENORS for d in DELTAS
        ])
        x_std = (x - means) / stds
        out[t] = loadings @ x_std
    return out


async def _purge(symbol: str, model_id: int) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        r = await s.execute(
            delete(PcaSignal)
            .where(PcaSignal.symbol == symbol)
            .where(PcaSignal.pca_model_id == model_id)
        )
        await s.commit()
        return r.rowcount or 0


async def main(symbol: str, shock_pc1: float, purge: bool) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        model = (await s.execute(
            select(PcaModel).where(PcaModel.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()
        if model is None:
            sys.exit(
                "no active PcaModel — run scripts/dev/seed_pca_snapshots.py first "
                "and click 'Refit PCA' in the UI"
            )

        snaps = (await s.execute(
            select(SurfaceSnapshotHourly)
            .where(SurfaceSnapshotHourly.symbol == symbol)
            .order_by(SurfaceSnapshotHourly.timestamp)
        )).scalars().all()
        if len(snaps) < 5:
            sys.exit(f"only {len(snaps)} snapshots — seed more first")

        rec_rows = (await s.execute(
            select(
                SignalRecommendationsMap.pc_id,
                SignalRecommendationsMap.signal_label,
                SignalRecommendationsMap.recommended_structure,
                SignalRecommendationsMap.default_tenor,
            ).where(SignalRecommendationsMap.is_active.is_(True))
        )).all()
        rec_map = {(r[0], r[1]): f"{r[2]}_{r[3]}" for r in rec_rows}

        means = np.asarray(model.means, dtype=float)
        stds = np.asarray(model.stds, dtype=float)
        loadings = np.asarray(model.loadings, dtype=float)
        var_ratio = list(model.variance_explained_ratio or [])

    raw = _project_history(snaps, means, stds, loadings)  # (T, n_comp)

    # Apply optional shock to last 3 cycles on PC1 → produces a persistent
    # actionable signal once z-scored.
    if shock_pc1:
        sigma_pc1 = float(raw[:, 0].std(ddof=1))
        boost = shock_pc1 * sigma_pc1
        raw[-3:, 0] += boost

    mu = raw.mean(axis=0)
    sigma = raw.std(axis=0, ddof=1)
    sigma = np.where(sigma <= 0, 1.0, sigma)
    z = (raw - mu) / sigma  # (T, n_comp)

    # Always wipe prior rows for the active model so the script is
    # idempotent (re-running with a different shock just rewrites them).
    deleted = await _purge(symbol, int(model.id))
    if deleted:
        print(f"purged {deleted} prior pca_signals rows for active model")

    sm = get_sessionmaker()
    async with sm() as s:
        for t, snap in enumerate(snaps):
            for pc_id in (1, 2, 3):
                idx = pc_id - 1
                z_t = float(z[t, idx])
                raw_t = float(raw[t, idx])
                label = classify_label(z_t)

                # stability proxy : same logic as engine.py:_compute_pca_signals
                cos_sim = getattr(model, f"cosine_similarity_pc{pc_id}", None)
                stable = cos_sim is None or float(cos_sim) >= 0.85
                ve = float(var_ratio[idx]) if idx < len(var_ratio) else 0.0

                # persistence : last 3 z's for this PC, most-recent-first
                z_history = [float(z[k, idx]) for k in range(t, max(-1, t - 3), -1)]
                persistent = is_persistent(z_history)

                flag = actionable_check(
                    pc_id=pc_id, z_score=z_t, label=label,
                    loadings_stable=stable, variance_explained=ve,
                    persistent=persistent,
                )
                rec = rec_map.get((pc_id, label)) if (label != "FAIR" and flag.actionable) else None

                s.add(PcaSignal(
                    timestamp=snap.timestamp, symbol=symbol,
                    pca_model_id=int(model.id), pc_id=pc_id,
                    raw_score=raw_t, z_score=z_t, label=label,
                    actionable=flag.actionable, actionable_reason=flag.reason,
                    sub_signals=None, recommended_structure=rec,
                ))
        await s.commit()
    print(f"inserted {len(snaps) * 3} rows in pca_signals (model id={model.id})")
    if shock_pc1:
        last_z = [float(z[-1, i]) for i in range(3)]
        print(f"  last cycle z-scores : pc1={last_z[0]:+.2f}  pc2={last_z[1]:+.2f}  pc3={last_z[2]:+.2f}")
        print("  → panel should show actionable PC1 with persistent signal")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument(
        "--shock-pc1", type=float, default=2.5,
        help="z-score shock applied to last 3 cycles on PC1 (default 2.5 → EXPENSIVE actionable)",
    )
    p.add_argument(
        "--purge", action="store_true",
        help="(no-op kept for back-compat — purge is now always done)",
    )
    args = p.parse_args()
    asyncio.run(main(args.symbol, args.shock_pc1, args.purge))
