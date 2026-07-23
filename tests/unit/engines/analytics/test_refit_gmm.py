"""_refit_gmm : fits the 3-component GMM on feature history and serializes it
to Redis. The model must round-trip via deserialize_gmm → infer_proba (2-D obs
matches the vol-engine's edge inference)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from bus import keys
from core.vol.gmm_regime import LABELS_3, MIN_OBS_GMM, deserialize_gmm, infer_proba
from engines.analytics.engine import AnalyticsEngine

from .conftest import FakeRedis, FakeResult, FakeSessionmaker


def _three_regime_rows(n_per: int = 20) -> list[tuple]:
    """Rows shaped (iv_atm_3m_pct, vol_of_vol_30d_pct, term_slope_pct) spanning
    calm / pre_event / stressed clusters. term_slope is present but unused."""
    rows: list[tuple] = []
    rng = np.random.default_rng(42)
    # (vol_level, vov) cluster centres.
    for center in ((6.0, 0.5), (10.0, 1.0), (15.0, 2.0)):
        for _ in range(n_per):
            vol = float(center[0] + rng.normal(0, 0.3))
            vov = float(center[1] + rng.normal(0, 0.1))
            slope = float(rng.normal(0, 0.2))
            rows.append((vol, vov, slope))
    return rows


@pytest.mark.asyncio
async def test_refit_gmm_serializes_and_round_trips():
    rows = _three_regime_rows(20)  # 60 rows ≥ MIN_OBS_GMM
    assert len(rows) >= MIN_OBS_GMM
    redis = FakeRedis()
    sm = FakeSessionmaker([FakeResult(rows=rows)])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._refit_gmm()

    key = keys.LATEST_REGIME_MODEL.format(symbol="EURUSD")
    assert key in redis.sets
    value, ex = redis.sets[key]
    assert ex == keys.TTL_ANALYTICS

    gmm, fit = deserialize_gmm(json.loads(value))
    # 2-D live obs — same dimensionality the vol-engine feeds at the edge.
    res = infer_proba(gmm, np.asarray([12.0, 1.5]), fit)
    assert res.label in LABELS_3
    total = res.p_calm + res.p_stressed + res.p_pre_event
    assert total == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_refit_gmm_skips_below_min_obs():
    rows = _three_regime_rows(5)  # 15 rows < MIN_OBS_GMM
    assert len(rows) < MIN_OBS_GMM
    redis = FakeRedis()
    sm = FakeSessionmaker([FakeResult(rows=rows)])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._refit_gmm()

    assert keys.LATEST_REGIME_MODEL.format(symbol="EURUSD") not in redis.sets


@pytest.mark.asyncio
async def test_refit_gmm_skips_rows_missing_a_feature():
    # Enough rows overall, but all have a None vol_of_vol → no usable training
    # pairs → no fit, no set.
    rows = [(8.0, None, 0.1) for _ in range(60)]
    redis = FakeRedis()
    sm = FakeSessionmaker([FakeResult(rows=rows)])
    engine = AnalyticsEngine(redis, sessionmaker=sm)

    await engine._refit_gmm()

    assert keys.LATEST_REGIME_MODEL.format(symbol="EURUSD") not in redis.sets
