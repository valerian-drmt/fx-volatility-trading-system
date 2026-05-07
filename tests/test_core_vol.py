"""Snapshot + equivalence tests for the vol helpers under ``core.vol.*``."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.vol.garch import fit_and_project_garch
from core.vol.pchip_smile import DELTA_LABELS, interpolate_delta_pillars
from core.vol.yang_zhang import yang_zhang_rv_pct

# ── yang-zhang ─────────────────────────────────────────────────────────

def _flat_ohlc(n: int, close: float = 1.08, vol_pct: float = 1.0) -> pd.DataFrame:
    """Synthetic OHLC frame : each row a small random shock around ``close``."""
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(0, vol_pct / 100, size=n)
    closes = close * np.cumprod(1 + returns)
    opens = closes * (1 + rng.normal(0, vol_pct / 400, size=n))
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, vol_pct / 400, size=n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, vol_pct / 400, size=n)))
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


@pytest.mark.unit
def test_yang_zhang_returns_none_for_short_window():
    df = _flat_ohlc(2)
    assert yang_zhang_rv_pct(df, window=2) is None


@pytest.mark.unit
def test_yang_zhang_produces_positive_number_on_real_shape_frame():
    df = _flat_ohlc(60, vol_pct=1.0)
    rv = yang_zhang_rv_pct(df, window=60)
    assert rv is not None
    # With ~1% daily shocks, annualised rv should land somewhere between 5% and 30%.
    assert 2.0 < rv < 40.0


# ── pchip smile ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_pchip_interpolates_atm_exactly_when_present():
    # Observations span 0.1 → 0.9 delta with a symmetric smile.
    obs = [
        (0.10, 0.085, 1.12),
        (0.25, 0.078, 1.10),
        (0.50, 0.072, 1.08),
        (0.75, 0.079, 1.06),
        (0.90, 0.086, 1.04),
    ]
    out = interpolate_delta_pillars(obs)
    # PCHIP passes through the observations exactly.
    assert out["atm"].iv == pytest.approx(0.072, abs=1e-9)
    assert out["atm"].strike == pytest.approx(1.08, abs=1e-9)
    assert out["25dc"].iv == pytest.approx(0.078, abs=1e-9)
    assert out["25dp"].iv == pytest.approx(0.079, abs=1e-9)


@pytest.mark.unit
def test_pchip_returns_none_outside_observed_range():
    obs = [(0.25, 0.078, 1.10), (0.50, 0.072, 1.08), (0.75, 0.079, 1.06)]
    out = interpolate_delta_pillars(obs)
    # 10∆ call / put are outside [0.25, 0.75] → None.
    assert out["10dc"].iv is None
    assert out["10dc"].strike is None
    assert out["10dp"].iv is None


@pytest.mark.unit
def test_pchip_bails_out_on_fewer_than_three_points():
    out = interpolate_delta_pillars([(0.25, 0.078, 1.10), (0.50, 0.072, 1.08)])
    assert all(p.iv is None and p.strike is None for p in out.values())


@pytest.mark.unit
def test_delta_labels_are_exhaustive():
    assert set(DELTA_LABELS) == {"atm", "25dc", "25dp", "10dc", "10dp"}


# ── garch ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_garch_empty_when_insufficient_data():
    assert fit_and_project_garch(np.array([1.0, 1.01, 1.02]), tenor_t={"1M": 1 / 12}) == {}


@pytest.mark.unit
def test_garch_returns_positive_sigmas_for_each_tenor():
    rng = np.random.default_rng(seed=123)
    # 200 days of 0.8% daily shocks around 1.08.
    returns = rng.normal(0, 0.008, size=200)
    closes = 1.08 * np.cumprod(1 + returns)

    tenor_t = {"1M": 1 / 12, "3M": 3 / 12, "1Y": 1.0}
    out = fit_and_project_garch(closes, tenor_t=tenor_t)

    assert set(out) == set(tenor_t)
    for label in tenor_t:
        sigma = out[label]["sigma_model_pct"]
        assert 1.0 < sigma < 40.0, f"{label}={sigma}% out of sanity range"


@pytest.mark.unit
def test_garch_empirical_blend_tends_to_rv_when_blend_is_zero():
    rng = np.random.default_rng(seed=7)
    closes = 1.08 * np.cumprod(1 + rng.normal(0, 0.01, size=150))

    rv_map = {"1M": {"RV_pct": 8.0}}
    out = fit_and_project_garch(
        closes, tenor_t={"1M": 1 / 12}, rv_map=rv_map, rv_full=12.0,
        blend=0.0, emp_kappa=2.0,
    )
    # blend=0 → pure empirical leg : rv_full + (rv_tenor - rv_full) * exp(-kappa*T)
    expected = 12.0 + (8.0 - 12.0) * np.exp(-2.0 / 12)
    assert out["1M"]["sigma_model_pct"] == pytest.approx(expected, abs=1e-3)
