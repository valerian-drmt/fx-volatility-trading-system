"""_collect_snapshot : builds the 30-dim PCA snapshot from the Redis surface,
gated on the last SurfaceSnapshotHourly timestamp."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from bus import keys
from engines.analytics.engine import AnalyticsEngine

from .conftest import FakeRedis, FakeResult, FakeSessionmaker, make_surface


@pytest.mark.asyncio
async def test_collect_snapshot_publishes_when_no_prior_snapshot(monkeypatch):
    monkeypatch.setenv("PCA_SNAPSHOT_INTERVAL_MIN", "60")
    redis = FakeRedis({
        keys.LATEST_VOL_SURFACE.format(symbol="EURUSD"): json.dumps(
            {"symbol": "EURUSD", "surface": make_surface()}
        ),
        keys.LATEST_SPOT.format(symbol="EURUSD"): "1.10500",
    })
    sm = FakeSessionmaker([FakeResult(scalar=None)])  # no prior snapshot → gate open
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._collect_snapshot()

    frames = redis.db_events()
    assert len(frames) == 1
    frame = frames[0]
    assert frame["table"] == "pca_surface_snapshot_history"
    payload = frame["payload"]
    iv_keys = [k for k in payload if k.startswith("iv_")]
    assert len(iv_keys) == 30
    assert payload["symbol"] == "EURUSD"
    assert payload["n_strikes_present"] == 30
    assert payload["spot_at_snapshot"] == pytest.approx(1.105)


@pytest.mark.asyncio
async def test_collect_snapshot_gated_when_recent(monkeypatch):
    monkeypatch.setenv("PCA_SNAPSHOT_INTERVAL_MIN", "60")
    last = datetime.now(UTC) - timedelta(minutes=5)  # 5 min < 60*0.9 → gated
    redis = FakeRedis({
        keys.LATEST_VOL_SURFACE.format(symbol="EURUSD"): json.dumps(
            {"symbol": "EURUSD", "surface": make_surface()}
        ),
    })
    sm = FakeSessionmaker([FakeResult(scalar=last)])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._collect_snapshot()

    assert redis.db_events() == []


@pytest.mark.asyncio
async def test_collect_snapshot_skips_when_surface_missing():
    redis = FakeRedis()  # no latest_vol_surface key
    sm = FakeSessionmaker([FakeResult(scalar=None)])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._collect_snapshot()

    assert redis.db_events() == []
