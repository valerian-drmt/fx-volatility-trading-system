"""Unit tests for core.vol.feature_enrichment_stamp.stamp_enrichment."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from core.vol.feature_enrichment_stamp import stamp_enrichment


def test_stamp_enrichment_writes_all_keys():
    rng = np.random.default_rng(0)
    z_history = {f: rng.standard_normal(500).tolist() for f in (
        "vol_level", "vol_of_vol", "term_slope",
    )}
    value_history = {
        "vol_level": rng.uniform(5, 8, 500).tolist(),
        "vol_of_vol": rng.uniform(0.1, 0.3, 500).tolist(),
        "term_slope": rng.uniform(-0.5, 0.5, 500).tolist(),
    }
    snap = {
        "vol_level_z": -0.15, "vol_level_pct": 6.25,
        "vol_of_vol_z": -0.43, "vol_of_vol_pct": 0.18,
        "term_slope_z": -2.07, "term_slope_pct": 0.06,
    }
    out = stamp_enrichment(snap, z_history=z_history, value_history=value_history)
    for f in ("vol_level", "vol_of_vol", "term_slope"):
        assert f"bucket_{f}" in out
        assert f"pct_{f}" in out
        assert f"signal_{f}" in out
        assert f"delta_z_1h_{f}" in out
    # term_slope z=-2.07 ≤ q025≈-1.96 → bucket "--"
    assert out["bucket_term_slope"] == "--"


def test_stamp_enrichment_delta_z_with_recent_slope():
    rng = np.random.default_rng(1)
    z_history = {f: rng.standard_normal(500).tolist() for f in (
        "vol_level", "vol_of_vol", "term_slope",
    )}
    value_history = {f: rng.uniform(0, 1, 500).tolist() for f in (
        "vol_level", "vol_of_vol", "term_slope",
    )}
    snap = {f"{f}_z": 0.0 for f in ("vol_level", "vol_of_vol", "term_slope")}
    snap.update({f"{f}_pct": 0.5 for f in ("vol_level", "vol_of_vol", "term_slope")})

    base = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    # 12 ticks at 5-min intervals, z growing +0.1 per tick → +1.2 z/h
    recent = {
        f: [(base + timedelta(minutes=5 * i), 0.1 * i) for i in range(12)]
        for f in ("vol_level", "vol_of_vol", "term_slope")
    }
    out = stamp_enrichment(
        snap, z_history=z_history, value_history=value_history,
        recent_z=recent, now=base + timedelta(minutes=55),
    )
    assert out["delta_z_1h_vol_level"] is not None
    assert abs(out["delta_z_1h_vol_level"] - 1.2) < 0.05


def test_stamp_enrichment_does_not_mutate_input():
    snap = {"vol_level_z": 1.0, "vol_level_pct": 6.0,
            "vol_of_vol_z": 0.0, "vol_of_vol_pct": 0.2,
            "term_slope_z": 0.0, "term_slope_pct": 0.0}
    snapshot_copy = dict(snap)
    stamp_enrichment(snap, z_history={}, value_history={})
    assert snap == snapshot_copy
