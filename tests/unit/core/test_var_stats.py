"""One VaR helper (#316, audit 03#3 / 03#12): numpy's linear percentile,
positive clamped losses, finite values only, diversification in [0, 100];
``component_var`` and ``/var`` both read from it."""
from __future__ import annotations

import math

import numpy as np
import pytest

from core.risk.marginal_var import component_var
from core.risk.var_stats import (
    MIN_OBS,
    diversification_pct,
    finite,
    loss_at,
    quantile,
    var_summary,
)


def test_quantile_matches_numpy_linear() -> None:
    xs = sorted([3.0, -1.5, 8.0, 0.25, -4.0, 2.0, 7.5, -0.5, 1.0, 5.0])
    for q in (0.0, 0.01, 0.05, 0.5, 0.95, 1.0):
        assert quantile(xs, q) == pytest.approx(float(np.percentile(xs, q * 100.0)))
    assert quantile([], 0.5) is None
    assert quantile([4.0], 0.01) == 4.0


def test_finite_drops_nan_and_inf_and_junk() -> None:
    assert finite([1.0, float("nan"), 2, float("inf"), "3.5", None, "x", -float("inf")]) == [1.0, 2.0, 3.5]


def test_loss_is_positive_and_a_gaining_tail_reads_zero() -> None:
    pnl = [-120.0, -80.0, -10.0, 5.0, 30.0, 45.0, 60.0, 90.0, 110.0, 150.0]
    assert loss_at(pnl, 0.99) == pytest.approx(-quantile(sorted(pnl), 0.01))
    assert loss_at([5.0, 6.0, 7.0, 8.0, 9.0], 0.99) == 0.0, "every scenario gains: no loss"
    assert loss_at([float("nan"), -5.0, -4.0, -3.0, -2.0, -1.0], 0.95) == loss_at([-5.0, -4.0, -3.0, -2.0, -1.0], 0.95)


def test_summary_is_ordered_clamped_and_needs_min_obs() -> None:
    assert var_summary([-1.0] * (MIN_OBS - 1)) is None
    assert var_summary([-1.0] * MIN_OBS) is not None
    pnl = [-500.0, -300.0, -200.0, -50.0, 0.0, 20.0, 80.0, 120.0, 200.0, 350.0]
    s = var_summary(pnl)
    assert s is not None and s.n == 10
    assert 0.0 <= s.var_95 <= s.var_99 <= s.es_99
    assert s.es_99 == pytest.approx(-pnl[0]), "ES at the 1 % quantile of 10 points is the worst scenario"
    # NaN scenarios do not count: same numbers as without them, n unchanged.
    s2 = var_summary([*pnl, float("nan"), float("inf")])
    assert s2 == s
    # All-gain series: losses clamp to 0 and stay ordered.
    g = var_summary([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert g is not None and (g.var_95, g.var_99, g.es_99) == (0.0, 0.0, 0.0)


def test_diversification_is_clamped_to_0_100() -> None:
    assert diversification_pct(50.0, 100.0) == pytest.approx(50.0)
    assert diversification_pct(150.0, 100.0) == 0.0, "a portfolio VaR above the sum of standalones is not negative diversification"
    assert diversification_pct(0.0, 100.0) == 100.0
    assert diversification_pct(10.0, 0.0) == 0.0


def test_component_var_drops_a_non_finite_series_and_says_so() -> None:
    a = [1.0, -2.0, 3.0, -1.0, 2.0, -3.0, 1.0, -2.0, 4.0, -1.0]
    b = [-1.0, 2.0, -2.0, 1.0, -1.0, 2.0, 0.0, 1.0, -3.0, 2.0]
    bad = [*b[:-1], math.nan]
    r = component_var({"a": a, "b": b, "c": bad}, conf=0.95)
    assert [p["id"] for p in r["positions"]] == ["a", "b"]
    assert r["dropped"] == [{"id": "c", "reason": "non_finite_pnl"}]
    assert 0.0 <= r["diversification_pct"] <= 100.0
    assert r["portfolio_var_usd"] == pytest.approx(loss_at(np.add(a, b), 0.95), abs=0.01)
    assert all(math.isfinite(p["component_usd"]) for p in r["positions"])
