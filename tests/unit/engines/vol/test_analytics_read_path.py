"""VolEngine analytics-read path (VOL_INLINE_ANALYTICS=0): the vol-engine serves
the GMM regime model + PC3 history PUBLISHED by the analytics engine instead of
computing them inline — train centrally, infer at the edge. The cheap per-cycle
inference/z-score still runs here.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

import engines.vol.engine as vol_engine_mod
from core.vol.gmm_regime import fit_gmm, serialize_gmm
from engines.vol.engine import VolEngine

pytestmark = pytest.mark.unit


class _Redis:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    async def get(self, name: str) -> Any:
        return self._store.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        self._store[name] = value
        return True

    async def publish(self, channel: str, message: str) -> int:
        return 0


def _engine(store: dict[str, Any]) -> VolEngine:
    async def _no_chain(_f: float) -> dict:
        return {}

    return VolEngine(
        ib=None, redis=_Redis(store), symbol="EURUSD",
        ib_host="h", ib_port=1, client_id=2, fetch_fop_chain=_no_chain,
    )


@pytest.fixture
def analytics_mode(monkeypatch):
    """Flip the module flag to the analytics-read path (default is inline)."""
    monkeypatch.setattr(vol_engine_mod, "_INLINE_ANALYTICS", False)


def _published_model() -> str:
    rng = np.random.default_rng(0)
    X = np.vstack([
        rng.normal((5.0, 1.0), 0.6, size=(60, 2)),
        rng.normal((10.0, 2.0), 0.6, size=(60, 2)),
        rng.normal((16.0, 5.0), 0.6, size=(60, 2)),
    ])
    gmm, fit = fit_gmm(X)
    assert gmm is not None and fit is not None
    return json.dumps(serialize_gmm(gmm, fit))


async def test_get_gmm_model_reads_published_model(analytics_mode):
    store = {"analytics:regime_model:EURUSD": _published_model()}
    eng = _engine(store)
    # feature_history is ignored in analytics mode — no inline fit.
    gmm, fit = await eng._get_gmm_model([])
    assert gmm is not None and fit is not None and fit.converged
    proba = gmm.predict_proba(np.array([[16.0, 5.0]]))
    assert proba.shape == (1, 3)
    assert proba.sum() == pytest.approx(1.0, abs=1e-6)


async def test_get_gmm_model_absent_returns_none(analytics_mode):
    eng = _engine({})  # nothing published yet (cold start)
    assert await eng._get_gmm_model([]) == (None, None)


async def test_read_pc3_history_from_analytics(analytics_mode):
    store = {
        "analytics:pc3_history:EURUSD": json.dumps(
            {"skew": [1.0, 2.0, 3.0], "convex": [0.5, 0.6, 0.7]}
        )
    }
    skew, convex = await _engine(store)._read_pc3_history_from_analytics()
    assert skew == [1.0, 2.0, 3.0]
    assert convex == [0.5, 0.6, 0.7]


async def test_read_pc3_history_absent_returns_empty():
    # Absent key → empty arrays (zscore_against degrades to 0), no crash.
    skew, convex = await _engine({})._read_pc3_history_from_analytics()
    assert skew == [] and convex == []
