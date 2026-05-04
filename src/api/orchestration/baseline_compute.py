"""Compute the per-(feature, event_type, days_bucket, tod_bucket) baseline.

Sweeps every historical ``regime_snapshots`` row, groups by context, writes
μ + σ + n_obs to ``vol_features_context_baseline``. Idempotent (UPSERT).

Pure-ish : the only side-effect is the UPSERT loop. The bucketing math is
shared with ``api.orchestration.regime_features._lookup_baseline`` so the
batch and the live read agree on the cell coordinates.

Called by :
  * ``api.orchestration.baseline_scheduler.BaselineScheduler`` (weekly Sunday 00:00 UTC)
  * ``scripts/dev/compute_context_baseline.py`` (one-shot CLI for ops)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import RegimeSnapshot

logger = logging.getLogger(__name__)

FEATURES: tuple[str, ...] = ("vol_level", "vol_of_vol", "term_slope")
DAYS_BUCKETS: tuple[tuple[int, int, int], ...] = (
    (0, 1, 0), (2, 3, 1), (4, 5, 2), (6, 10, 3),
)
TOD_BUCKETS: tuple[str, ...] = ("london_open", "overlap", "ny_close", "asia")
DAYS_BUCKET_OPEN = 4   # ">10 days" or null
INSUFFICIENT_N = 20


def days_bucket(days_to_event: float | None) -> int:
    """Discretise ``days_to_next_event`` into 5 buckets : [0-1,2-3,4-5,6-10,>10]."""
    if days_to_event is None:
        return DAYS_BUCKET_OPEN
    d = int(days_to_event)
    for lo, hi, idx in DAYS_BUCKETS:
        if lo <= d <= hi:
            return idx
    return DAYS_BUCKET_OPEN


def tod_bucket(ts: datetime) -> str:
    """Discretise the UTC hour into trading-day quarters.

    Naive datetimes (e.g. from a SQLite test backend) are treated as already
    being in UTC — astimezone() on a naive datetime would shift by the local
    TZ and give wrong buckets at the boundary.
    """
    if ts.tzinfo is None:
        h = ts.hour
    else:
        h = ts.astimezone(UTC).hour
    if 7 <= h < 12:
        return "london_open"
    if 12 <= h < 16:
        return "overlap"
    if 16 <= h < 22:
        return "ny_close"
    return "asia"


_UPSERT_SQL = """
INSERT INTO vol_features_context_baseline
    (feature, event_type, days_bucket, tod_bucket, mu, sigma, n_obs, status, computed_at)
VALUES (:feature, :event_type, :days_bucket, :tod_bucket, :mu, :sigma, :n_obs, :status, :computed_at)
ON CONFLICT (feature, event_type, days_bucket, tod_bucket) DO UPDATE SET
    mu = EXCLUDED.mu,
    sigma = EXCLUDED.sigma,
    n_obs = EXCLUDED.n_obs,
    status = EXCLUDED.status,
    computed_at = EXCLUDED.computed_at
"""


async def compute_baseline(db: AsyncSession) -> dict[str, Any]:
    """UPSERT every cell ; return ``{"valid": N, "insufficient": M, "total_cells": K}``."""
    rows = (await db.execute(
        select(
            RegimeSnapshot.timestamp,
            RegimeSnapshot.next_event_type,
            RegimeSnapshot.days_to_next_event,
            RegimeSnapshot.vol_level_z,
            RegimeSnapshot.vol_of_vol_z,
            RegimeSnapshot.term_slope_z,
        )
    )).all()

    buckets: dict[tuple[str, int, str], dict[str, list[float]]] = {}
    for ts, ev_type, days, z_l, z_v, z_s in rows:
        ev = (ev_type or "none").upper() if ev_type else "none"
        db_idx = days_bucket(float(days) if days is not None else None)
        tod = tod_bucket(ts)
        cell = buckets.setdefault((ev, db_idx, tod), {f: [] for f in FEATURES})
        if z_l is not None:
            cell["vol_level"].append(float(z_l))
        if z_v is not None:
            cell["vol_of_vol"].append(float(z_v))
        if z_s is not None:
            cell["term_slope"].append(float(z_s))

    now = datetime.now(UTC)
    n_valid = 0
    n_insufficient = 0
    for (ev, db_idx, tod), per_feat in buckets.items():
        for f, zs in per_feat.items():
            n = len(zs)
            mu = float(np.mean(zs)) if zs else 0.0
            sigma = float(np.std(zs, ddof=1)) if n > 1 else 0.0
            status = "valid" if n >= INSUFFICIENT_N else "insufficient"
            await db.execute(text(_UPSERT_SQL), {
                "feature": f, "event_type": ev, "days_bucket": db_idx,
                "tod_bucket": tod, "mu": mu, "sigma": sigma, "n_obs": n,
                "status": status, "computed_at": now,
            })
            if status == "valid":
                n_valid += 1
            else:
                n_insufficient += 1
    await db.commit()
    return {
        "valid": n_valid,
        "insufficient": n_insufficient,
        "total_cells": n_valid + n_insufficient,
    }
