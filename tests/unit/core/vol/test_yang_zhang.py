"""Tests for ``core.vol.yang_zhang`` — annualised realised-vol estimator.

Resurrected from ``tests/old/test_core_vol.py`` (git 14175622~1).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.vol.yang_zhang import yang_zhang_rv_pct

pytestmark = pytest.mark.unit


def _flat_ohlc(n: int, close: float = 1.08, vol_pct: float = 1.0) -> pd.DataFrame:
    """Synthetic OHLC frame : each row a small random shock around ``close``."""
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(0, vol_pct / 100, size=n)
    closes = close * np.cumprod(1 + returns)
    opens = closes * (1 + rng.normal(0, vol_pct / 400, size=n))
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, vol_pct / 400, size=n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, vol_pct / 400, size=n)))
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


def test_yang_zhang_returns_none_for_short_window():
    df = _flat_ohlc(2)
    assert yang_zhang_rv_pct(df, window=2) is None


def test_yang_zhang_produces_positive_number_on_real_shape_frame():
    df = _flat_ohlc(60, vol_pct=1.0)
    rv = yang_zhang_rv_pct(df, window=60)
    assert rv is not None
    # With ~1% daily shocks, annualised rv should land somewhere between 5% and 30%.
    assert 2.0 < rv < 40.0
