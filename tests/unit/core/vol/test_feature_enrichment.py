"""Unit tests for core.vol.feature_enrichment.

Covers the spec acceptance cases (E1 brief) :
  1. discretisation on z = [-3, -2.5, -1.8, -0.5, 0.5, 1.8, 2.5, 3]
  2. delta_z_1h on a linear ramp
  3. signal classifier on the four representative cases
  4. interpret_delta sign per feature
Plus a couple of robustness tests around insufficient history.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.vol.feature_enrichment import (
    bucket,
    delta_z_1h,
    interpret_delta,
    pct,
    signal,
)

# ─────────────────────────────────────────────────────────────────────────
# 1. bucket()
# ─────────────────────────────────────────────────────────────────────────

def test_bucket_uniform_history_5_buckets():
    """Spec test 1 : on a long N(0,1) z-history, the empirical quantiles
    converge to the textbook gaussian cutoffs (q025≈-1.96, q160≈-1.0,
    q840≈+1.0, q975≈+1.96), so the spec test cases discretise as expected.
    """
    rng = np.random.default_rng(0)
    history = rng.standard_normal(size=20000).tolist()
    cases: list[tuple[float, str]] = [
        (-3.0, "--"),
        (-2.5, "--"),
        (-1.8, "-"),
        (-0.5, "0"),
        (0.5, "0"),
        (1.8, "+"),
        (2.5, "++"),
        (3.0, "++"),
    ]
    for z_val, expected in cases:
        assert bucket(z_val, history) == expected, f"z={z_val} → expected {expected}"


def test_bucket_falls_back_to_normal_when_history_short():
    """< 10 observations → standard-normal cutoffs (z=±1, ±2)."""
    short = [0.0, 0.1, -0.1]
    assert bucket(-2.5, short) == "--"
    assert bucket(-1.5, short) == "-"
    assert bucket(0.0, short) == "0"
    assert bucket(1.5, short) == "+"
    assert bucket(2.5, short) == "++"


def test_bucket_ignores_nan_in_history():
    """NaN entries are filtered ; the remaining sample drives the cutoffs."""
    rng = np.random.default_rng(1)
    clean = rng.uniform(-2.0, 2.0, size=500).tolist()
    poisoned = clean + [float("nan")] * 50
    assert bucket(-1.95, poisoned) == "--"


# ─────────────────────────────────────────────────────────────────────────
# 2. delta_z_1h()
# ─────────────────────────────────────────────────────────────────────────

def test_delta_z_1h_linear_ramp_recovers_slope():
    """Spec test 2 : z grows by +0.1 per 5-min tick over 12 ticks → slope
    in z-points / hour is +1.2 (12 ticks × 0.1 / 60 minutes × 60)."""
    minutes = [5.0 * i for i in range(12)]
    z_vals = [0.1 * i for i in range(12)]
    slope = delta_z_1h(minutes, z_vals)
    assert slope is not None
    assert slope == pytest.approx(1.2, abs=0.05)


def test_delta_z_1h_returns_none_below_min_points():
    minutes = [0.0, 5.0, 10.0]
    z_vals = [0.0, 0.1, 0.2]
    assert delta_z_1h(minutes, z_vals) is None


def test_delta_z_1h_handles_constant_timestamps():
    minutes = [10.0] * 12
    z_vals = [0.1 * i for i in range(12)]
    assert delta_z_1h(minutes, z_vals) is None


# ─────────────────────────────────────────────────────────────────────────
# 3. pct()
# ─────────────────────────────────────────────────────────────────────────

def test_pct_median_returns_50():
    history = list(range(1, 101))            # 1..100
    assert pct(50.5, history) == 50


def test_pct_below_min_returns_zero():
    history = list(range(10, 30))
    assert pct(5.0, history) == 0


def test_pct_above_max_returns_hundred():
    history = list(range(10, 30))
    assert pct(35.0, history) == 100


def test_pct_empty_history_returns_none():
    assert pct(1.0, []) is None


# ─────────────────────────────────────────────────────────────────────────
# 4. signal()
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("z", "pct_value", "expected"),
    [
        (0.5, 50, "noise"),     # spec case 1
        (1.2, 88, "weak"),      # spec case 2 (pct just outside [10,90])
        (-1.92, 3, "strong"),   # spec case 3
        (2.7, 99, "tail"),      # spec case 4 (|z| ≥ 2.5)
    ],
)
def test_signal_spec_cases(z, pct_value, expected):
    assert signal(z, pct_value) == expected


def test_signal_unknown_pct_keeps_classification():
    """``None`` pct doesn't downgrade a clear z-signal."""
    assert signal(2.0, None) == "tail"          # |z|=2.0 + pct unknown → not strong (pct<1 or >99 path)
    # The "strong" branch needs pct ∈ (1, 99) so without pct we land on tail.
    # That is the conservative call : without history, treat any |z|≥1.5 as tail-like.
    assert signal(1.6, None) == "tail"


# ─────────────────────────────────────────────────────────────────────────
# 5. interpret_delta()
# ─────────────────────────────────────────────────────────────────────────

def test_interpret_delta_vol_level_standard_sign():
    assert interpret_delta("vol_level", +0.5) == "underpriced"
    assert interpret_delta("vol_level", -0.5) == "overpriced"
    assert interpret_delta("vol_level", +0.10) == "aligned"
    assert interpret_delta("vol_level", -0.10) == "aligned"


def test_interpret_delta_vol_of_vol_standard_sign():
    assert interpret_delta("vol_of_vol", +0.4) == "underpriced"
    assert interpret_delta("vol_of_vol", -0.4) == "overpriced"


def test_interpret_delta_term_slope_inverted_sign():
    """For term_slope the sign convention flips : a delta < 0 (z_obs more
    negative than expected) means the market is pricing more short-tenor
    risk than the historical baseline → 'underpriced'."""
    assert interpret_delta("term_slope", -0.5) == "underpriced"
    assert interpret_delta("term_slope", +0.5) == "overpriced"
    assert interpret_delta("term_slope", -0.20) == "aligned"


def test_interpret_delta_unknown_feature_uses_default_sign():
    """Default to the vol_level convention so we never raise on a typo."""
    assert interpret_delta("unknown_feature", +0.5) == "underpriced"
