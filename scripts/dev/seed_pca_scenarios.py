"""Seed 5 named PCA scenarios into the DB so the UI can be tested against
each branch of the panel without waiting for live data.

Each scenario creates a PcaModel with ``version = "scenario_<name>"`` plus
3 PcaSignal rows (one per PC). The model is NEVER set ``is_active=True`` —
the live scheduler retains its independent active model.

Front-end consumes via ``GET /api/v1/signals/pca/state?scenario=<name>``.

Re-run is idempotent : the script deletes prior ``scenario_*`` rows first.

Scenarios :
  - actionable_pc1_cheap     : PC1 z=-2.0, all gates green, structure recommended
  - actionable_pc2_expensive : PC2 z=+1.8, calendar short recommended
  - blocked_low_variance     : PC3 would fire but variance < 5% → blocked
  - blocked_n_obs            : strong PC1 signal but n_obs=20 < hard floor → blocked
  - stale_data               : valid signals but timestamp 3h old → UI must flag stale
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import delete, select  # noqa: E402

from persistence.db import get_sessionmaker  # noqa: E402
from persistence.models import PcaModel, PcaSignal  # noqa: E402


# Synthetic loadings : PC1 ≈ level (all +), PC2 ≈ slope (tenor tilt),
# PC3 ≈ smile (wings vs ATM). 6 components stored, 3 used.
def _synthetic_loadings() -> np.ndarray:
    L = np.zeros((6, 30))
    # PC1 — level
    L[0] = 0.18
    # PC2 — slope (negative on short tenors, positive on long)
    tenor_w = np.linspace(-0.4, 0.4, 6)
    for ti in range(6):
        L[1, ti * 5:(ti + 1) * 5] = tenor_w[ti]
    # PC3 — smile (positive on wings, negative on ATM)
    smile_w = np.array([0.4, 0.1, -0.5, 0.1, 0.4])
    for ti in range(6):
        L[2, ti * 5:(ti + 1) * 5] = smile_w
    # PC4-6 noise
    rng = np.random.default_rng(0)
    L[3:] = 0.05 * rng.normal(size=(3, 30))
    return L


_MEANS = [7.0] * 30
_STDS = [0.5] * 30
_LOADINGS = _synthetic_loadings().tolist()


SCENARIOS: list[dict] = [
    {
        "name": "actionable_pc1_cheap",
        "var": [0.72, 0.18, 0.06, 0.03, 0.005, 0.005],
        "cos_sim": [1.0, 1.0, 1.0],
        "n_obs": 200,
        "ts_offset_min": 0,
        "signals": [
            # (pc_id, z, raw, label, actionable, reason, recommended)
            (1, -2.00, -1.234, "CHEAP", True, None, "straddle_atm_3M"),
            (2, 0.30, 0.087, "FAIR", False, "label_fair", "calendar_short_1M_3M"),
            (3, 0.40, 0.052, "FAIR", False, "label_fair", "short_butterfly_25d_3M"),
        ],
    },
    {
        "name": "actionable_pc2_expensive",
        "var": [0.65, 0.22, 0.08, 0.03, 0.01, 0.005],
        "cos_sim": [1.0, 1.0, 1.0],
        "n_obs": 200,
        "ts_offset_min": 0,
        "signals": [
            (1, 0.40, 0.221, "FAIR", False, "label_fair", "short_strangle_3M"),
            (2, 1.80, 0.412, "EXPENSIVE", True, None, "calendar_short_1M_3M"),
            (3, -0.30, -0.041, "FAIR", False, "label_fair", "long_butterfly_25d_3M"),
        ],
    },
    {
        "name": "blocked_low_variance",
        "var": [0.78, 0.16, 0.03, 0.02, 0.005, 0.005],  # PC3 = 3% < 5% min
        "cos_sim": [1.0, 1.0, 1.0],
        "n_obs": 200,
        "ts_offset_min": 0,
        "signals": [
            (1, 0.20, 0.110, "FAIR", False, "label_fair", "short_strangle_3M"),
            (2, -0.10, -0.034, "FAIR", False, "label_fair", "calendar_long_1M_3M"),
            (3, 2.50, 0.581, "EXPENSIVE", False, "low_variance_pc3", "short_butterfly_25d_3M"),
        ],
    },
    {
        "name": "blocked_n_obs",
        "var": [0.70, 0.20, 0.07, 0.02, 0.005, 0.005],
        "cos_sim": [1.0, 1.0, 1.0],
        "n_obs": 20,                    # < hard floor 30
        "ts_offset_min": 0,
        "signals": [
            (1, -2.00, -1.234, "CHEAP", False, "low_n_obs", "straddle_atm_3M"),
            (2, 0.30, 0.087, "FAIR", False, "low_n_obs", "calendar_short_1M_3M"),
            (3, 0.40, 0.052, "FAIR", False, "low_n_obs", "short_butterfly_25d_3M"),
        ],
    },
    {
        "name": "stale_data",
        "var": [0.72, 0.18, 0.06, 0.03, 0.005, 0.005],
        "cos_sim": [1.0, 1.0, 1.0],
        "n_obs": 200,
        "ts_offset_min": 180,            # 3h old → UI flags stale
        "signals": [
            (1, -2.00, -1.234, "CHEAP", True, None, "straddle_atm_3M"),
            (2, 0.30, 0.087, "FAIR", False, "label_fair", "calendar_short_1M_3M"),
            (3, 0.40, 0.052, "FAIR", False, "label_fair", "short_butterfly_25d_3M"),
        ],
    },
]


async def main() -> None:
    sm = get_sessionmaker()
    now = datetime.now(UTC)

    async with sm() as s:
        # Wipe prior scenario rows : signals first (FK), then models.
        prior_models = (await s.execute(
            select(PcaModel).where(PcaModel.version.like("scenario_%"))
        )).scalars().all()
        if prior_models:
            ids = [m.id for m in prior_models]
            await s.execute(delete(PcaSignal).where(PcaSignal.pca_model_id.in_(ids)))
            await s.execute(delete(PcaModel).where(PcaModel.id.in_(ids)))
            await s.commit()
            print(f"purged {len(prior_models)} prior scenario models + their signals")

    async with sm() as s:
        for sc in SCENARIOS:
            model = PcaModel(
                version=f"scenario_{sc['name']}",
                fit_window_start=now - timedelta(days=365),
                fit_window_end=now - timedelta(minutes=sc["ts_offset_min"]),
                n_obs_used=sc["n_obs"],
                means=_MEANS, stds=_STDS, loadings=_LOADINGS,
                eigenvalues=[v * sc["n_obs"] for v in sc["var"]],
                variance_explained_ratio=sc["var"],
                n_components_kept=6, is_active=False,
                cosine_similarity_pc1=sc["cos_sim"][0],
                cosine_similarity_pc2=sc["cos_sim"][1],
                cosine_similarity_pc3=sc["cos_sim"][2],
                sign_flip_pc1=False, sign_flip_pc2=False, sign_flip_pc3=False,
                notes=f"fixture scenario for UI testing — {sc['name']}",
            )
            s.add(model)
            await s.flush()

            ts = now - timedelta(minutes=sc["ts_offset_min"])
            for pc_id, z, raw, label, actionable, reason, rec in sc["signals"]:
                sub = None
                if pc_id == 3:
                    sub = {
                        "skew_z": round(z * 0.6, 2),
                        "convex_z": round(z * 0.9, 2),
                    }
                s.add(PcaSignal(
                    timestamp=ts, symbol="EURUSD",
                    pca_model_id=int(model.id), pc_id=pc_id,
                    raw_score=raw, z_score=z, label=label,
                    actionable=actionable, actionable_reason=reason,
                    sub_signals=sub, recommended_structure=rec,
                ))
        await s.commit()

    print(f"seeded {len(SCENARIOS)} scenarios :")
    for sc in SCENARIOS:
        print(f"  /api/v1/signals/pca/state?scenario={sc['name']}")


if __name__ == "__main__":
    asyncio.run(main())
