"""run_cycle : each step is best-effort — one failing step must not block the
others, and the cycle returns without raising."""
from __future__ import annotations

import json

import pytest
from _analytics_helpers import (
    FakeRedis,
    FakeResult,
    FakeSessionmaker,
    make_snapshot_row,
    make_surface,
)

from bus import keys
from engines.analytics.engine import AnalyticsEngine


@pytest.mark.asyncio
async def test_run_cycle_survives_a_failing_step(monkeypatch):
    monkeypatch.setenv("PCA_SNAPSHOT_INTERVAL_MIN", "60")
    from datetime import UTC, datetime

    redis = FakeRedis({
        keys.LATEST_VOL_SURFACE.format(symbol="EURUSD"): json.dumps(
            {"symbol": "EURUSD", "surface": make_surface()}
        ),
    })
    # Sessions opened, in order : step1 (snapshot last-ts) then step3 (pc3 rows).
    # step2 (_refit_gmm) is patched to raise before it opens a session.
    now = datetime.now(UTC)
    sm = FakeSessionmaker([
        FakeResult(scalar=None),                       # step1 : no prior snapshot
        FakeResult(scalars=[make_snapshot_row(now)]),  # step3 : one valid snapshot
    ])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    async def _boom() -> None:
        raise RuntimeError("gmm blew up")

    monkeypatch.setattr(engine, "_refit_gmm", _boom)

    # Must not raise despite the middle step failing.
    await engine.run_cycle()

    # Step 1 still published the snapshot db_event.
    frames = redis.db_events()
    assert any(f["table"] == "pca_surface_snapshot_history" for f in frames)
    # Step 3 still published the PC3 history.
    assert keys.LATEST_PC3_HISTORY.format(symbol="EURUSD") in redis.sets
    # Step 2 produced nothing.
    assert keys.LATEST_REGIME_MODEL.format(symbol="EURUSD") not in redis.sets
