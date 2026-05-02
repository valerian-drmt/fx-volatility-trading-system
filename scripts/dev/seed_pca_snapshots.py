"""Seed synthetic 30-dim hourly snapshots into ``surface_snapshots_hourly``.

User-runnable, sandbox-only. Lets the Step 2 PCA UI be exercised without
waiting for live IB data (markets closed → vol-engine produces no
complete surface → no real snapshot accumulates).

Usage (from project root, secrets loaded so DATABASE_URL is set) :

    python scripts/dev/seed_pca_snapshots.py            # 35 rows, EURUSD
    python scripts/dev/seed_pca_snapshots.py --n 60
    python scripts/dev/seed_pca_snapshots.py --purge    # wipe seed_dev rows first

After insertion, hit ``POST /api/v1/admin/pca/refit`` (or click the Refit
button on the Step2 dev page) to fit the first model.

The synthetic data has 3 latent factors (level, term-tilt, smile-breath)
+ small idiosyncratic noise so the SVD finds non-degenerate PCs.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

# Ensure src/ is on sys.path when running outside the docker images.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import delete  # noqa: E402

from persistence.db import get_sessionmaker  # noqa: E402
from persistence.models import SurfaceSnapshotHourly  # noqa: E402

TENORS = ("1m", "2m", "3m", "4m", "5m", "6m")
DELTAS = ("10dp", "25dp", "atm", "25dc", "10dc")
SEED_SOURCE = "seed_dev"


def _generate(n: int, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base_atm = np.array([6.80, 6.90, 7.00, 7.05, 7.10, 7.15])  # 6 tenors, % vol
    smile = np.array([0.60, 0.20, 0.00, 0.10, 0.45])           # 5 deltas, additive
    tenor_w = np.linspace(-1.0, 1.0, 6)                         # tilt loadings

    levels = rng.normal(0.0, 0.30, n)         # PC1 driver (level shift)
    tilts = rng.normal(0.0, 0.15, n)          # PC2 driver (term slope)
    breaths = rng.normal(0.0, 0.10, n)        # PC3 driver (smile breath)
    noise = rng.normal(0.0, 0.02, (n, 30))    # idiosyncratic

    X = np.zeros((n, 30))
    for k in range(n):
        for ti in range(6):
            for di in range(5):
                X[k, ti * 5 + di] = (
                    base_atm[ti] + smile[di]
                    + levels[k]
                    + tilts[k] * tenor_w[ti]
                    + breaths[k] * smile[di]
                )
        X[k] += noise[k]
    return X


async def _purge_seed_rows(symbol: str) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        result = await s.execute(
            delete(SurfaceSnapshotHourly)
            .where(SurfaceSnapshotHourly.symbol == symbol)
            .where(SurfaceSnapshotHourly.source == SEED_SOURCE)
        )
        await s.commit()
        return result.rowcount or 0


async def _insert(n: int, symbol: str) -> int:
    X = _generate(n)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    sm = get_sessionmaker()
    async with sm() as s:
        for k in range(n):
            ts = now - timedelta(hours=n - k)
            row = SurfaceSnapshotHourly(
                timestamp=ts, symbol=symbol, source=SEED_SOURCE,
                spot_at_snapshot=1.0850,
                n_strikes_present=30,
                has_no_arb_violation=False,
            )
            i = 0
            for t in TENORS:
                for d in DELTAS:
                    setattr(row, f"iv_{t}_{d}", float(X[k, i]))
                    i += 1
            s.add(row)
        await s.commit()
    return n


async def main(n: int, symbol: str, purge: bool) -> None:
    if purge:
        deleted = await _purge_seed_rows(symbol)
        print(f"purged {deleted} prior {SEED_SOURCE} rows for {symbol}")
    inserted = await _insert(n, symbol)
    print(f"inserted {inserted} synthetic snapshots into surface_snapshots_hourly")
    print(f"  symbol={symbol}  source={SEED_SOURCE}")
    print("next step: POST /api/v1/admin/pca/refit  (or click 'Refit PCA' in /dev/step2-pca)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--n", type=int, default=35, help="rows to insert (>=30 required for refit)")
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--purge", action="store_true", help=f"delete prior source='{SEED_SOURCE}' rows first")
    args = p.parse_args()
    asyncio.run(main(args.n, args.symbol, args.purge))
