"""Unit tests for core.risk.var_factors — scenario VaR by factor."""
from __future__ import annotations

from core.risk.var_factors import DEFAULT_SHOCKS, factor_var_breakdown


def _future(qty_signed: float, mult: float) -> dict:
    return {"type": "FUTURE", "qty_signed": qty_signed, "mult": mult}


def test_returns_four_factors_in_order():
    out = factor_var_breakdown([], 1.10)
    assert [f["key"] for f in out] == ["spot", "level", "skew", "curv"]
    assert all(f["var_usd"] == 0.0 for f in out)  # empty book → no risk


def test_future_loads_only_spot_factor():
    # A future has delta only → spot factor carries VaR, vol factors are zero.
    out = {f["key"]: f["var_usd"] for f in factor_var_breakdown([_future(10, 125_000)], 1.10)}
    assert out["spot"] > 0
    assert out["level"] == 0.0 and out["skew"] == 0.0 and out["curv"] == 0.0
    # spot 99% move = 150bp = 1.5% → loss = |qty·mult·spot·0.015|
    assert abs(out["spot"] - 10 * 125_000 * 1.10 * (DEFAULT_SHOCKS["spot"] / 10000.0)) < 1e-6


def test_var_is_nonnegative_loss():
    out = factor_var_breakdown([_future(-3, 125_000)], 1.20)
    assert all(f["var_usd"] >= 0 for f in out)  # always reported as a positive loss
