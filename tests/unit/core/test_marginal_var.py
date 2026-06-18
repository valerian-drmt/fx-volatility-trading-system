"""Unit tests for core.risk.marginal_var — component VaR decomposition."""
from __future__ import annotations

from core.risk.marginal_var import component_var


def test_empty_input():
    r = component_var({})
    assert r["positions"] == [] and r["portfolio_var_usd"] == 0.0


def test_insufficient_history_returns_empty():
    r = component_var({"a": [1.0, -2.0, 3.0]})  # 3 days < MIN_DAYS
    assert r["positions"] == [] and r["n_days"] == 3


def test_components_sum_to_portfolio_var():
    a = [1.0, -2.0, 3.0, -1.0, 2.0, -3.0, 1.0, -2.0, 4.0, -1.0]
    b = [-1.0, 2.0, -2.0, 1.0, -1.0, 2.0, 0.0, 1.0, -3.0, 2.0]
    r = component_var({"a": a, "b": b}, conf=0.95)
    assert len(r["positions"]) == 2
    # Euler additivity: Σ component VaR = portfolio VaR (within rounding).
    total = sum(p["component_usd"] for p in r["positions"])
    assert abs(total - r["portfolio_var_usd"]) < 0.05
    assert abs(sum(p["pct"] for p in r["positions"]) - 100.0) < 1.0


def test_offsetting_positions_diversify():
    a = [5.0, -5.0, 5.0, -5.0, 5.0, -5.0, 5.0, -5.0]
    b = [-5.0, 5.0, -5.0, 5.0, -5.0, 5.0, -5.0, 5.0]
    r = component_var({"a": a, "b": b}, conf=0.95)
    # the book nets to ~0 each day → portfolio VaR well below Σ standalone.
    assert r["diversification_pct"] > 0
