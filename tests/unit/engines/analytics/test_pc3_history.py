"""_recompute_pc3_history : recompute skew/convex from the latest snapshots
and publish to Redis. Null-IV rows are skipped."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from bus import keys
from engines.analytics.engine import AnalyticsEngine

from .conftest import FakeRedis, FakeResult, FakeSessionmaker, make_snapshot_row


@pytest.mark.asyncio
async def test_pc3_history_publishes_skew_convex_arrays():
    now = datetime.now(UTC)
    # 5 rows, ordered desc ; the 2nd has null IVs and must be skipped → len 4.
    rows = [
        make_snapshot_row(now - timedelta(hours=0), bias=0.0),
        make_snapshot_row(now - timedelta(hours=1), null=True),
        make_snapshot_row(now - timedelta(hours=2), bias=0.2),
        make_snapshot_row(now - timedelta(hours=3), bias=0.4),
        make_snapshot_row(now - timedelta(hours=4), bias=0.6),
    ]
    redis = FakeRedis()
    sm = FakeSessionmaker([FakeResult(scalars=rows)])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._recompute_pc3_history()

    key = keys.LATEST_PC3_HISTORY.format(symbol="EURUSD")
    assert key in redis.sets
    value, ex = redis.sets[key]
    assert ex == keys.TTL_ANALYTICS
    payload = json.loads(value)
    assert len(payload["skew"]) == 4
    assert len(payload["convex"]) == 4
    # latest_snapshot_ts is the newest row's timestamp (rows[0]).
    assert payload["latest_snapshot_ts"] == rows[0].timestamp.isoformat().replace(
        "+00:00", "Z"
    )
    assert payload["fit_timestamp"] is not None


@pytest.mark.asyncio
async def test_pc3_history_empty_when_no_rows():
    redis = FakeRedis()
    sm = FakeSessionmaker([FakeResult(scalars=[])])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._recompute_pc3_history()

    key = keys.LATEST_PC3_HISTORY.format(symbol="EURUSD")
    assert key in redis.sets
    payload = json.loads(redis.sets[key][0])
    assert payload["skew"] == []
    assert payload["convex"] == []
    assert payload["latest_snapshot_ts"] is None
