"""Unit tests for core.risk.hist_var — historical-simulation shocks + book replay."""
from __future__ import annotations

import math

import pytest

from core.risk.hist_var import (
    RV_WINDOW,
    TRADING_DAYS,
    daily_returns,
    market_shocks,
    portfolio_pnl,
    rolling_rv_vp,
    simulate_pnl_by_position,
)


def _future(qty_signed: float, mult: float = 125_000, pid: str = "f1") -> dict:
    return {"id": pid, "type": "FUTURE", "qty_signed": qty_signed, "mult": mult}


def _option(qty_signed: float, strike: float, iv: float, right: str, pid: str = "o1") -> dict:
    from core.pricing.bs import bs_price

    T = 30 / 365.0
    return {
        "id": pid, "type": "OPTION", "qty_signed": qty_signed, "mult": 125_000,
        "K": strike, "T": T, "iv": iv, "right": right,
        "price_base": bs_price(1.10, strike, T, iv, right),
    }


def _walk(n: int, step: float = 0.004) -> list[float]:
    """Deterministic zig-zag close series with a non-constant amplitude, so the
    realised-vol window actually moves day to day."""
    closes = [1.10]
    for i in range(n):
        amp = step * (1.0 + 0.5 * math.sin(i / 3.0))
        closes.append(closes[-1] * (1.0 + (amp if i % 2 == 0 else -amp)))
    return closes


# ── shock construction ────────────────────────────────────────────────────────

def test_daily_returns_skips_non_positive_closes():
    # A bad bar (0 / negative) must not inject a ±100% scenario.
    assert daily_returns([1.0, 1.1, 0.0, 1.2]) == pytest.approx([0.1])


def test_daily_returns_empty_below_two_points():
    assert daily_returns([]) == [] and daily_returns([1.10]) == []


def test_rolling_rv_is_none_until_the_window_is_full():
    rv = rolling_rv_vp([0.01] * 30, window=RV_WINDOW)
    assert rv[: RV_WINDOW - 1] == [None] * (RV_WINDOW - 1)
    assert all(v is not None for v in rv[RV_WINDOW - 1 :])


def test_rolling_rv_annualises_in_vol_points():
    # Alternating ±1% → sample stdev of the window is ~1% (mean ≈ 0).
    rets = [0.01 if i % 2 == 0 else -0.01 for i in range(40)]
    rv = rolling_rv_vp(rets, window=20)[-1]
    assert rv == pytest.approx(0.01 * math.sqrt(TRADING_DAYS) * 100.0, rel=0.05)


def test_rolling_rv_rejects_degenerate_window():
    with pytest.raises(ValueError):
        rolling_rv_vp([0.01, 0.02], window=1)


def test_market_shocks_pairs_spot_bp_with_vol_points():
    closes = _walk(120)
    shocks = market_shocks(closes)
    # One scenario per session that has a full RV window on both d and d-1.
    assert len(shocks) == len(closes) - 1 - RV_WINDOW
    dspot, dvol = zip(*shocks, strict=True)
    # ~40bp zig-zag moves, and a vol proxy that actually varies.
    assert max(abs(x) for x in dspot) > 10.0
    assert any(abs(v) > 0 for v in dvol)


def test_market_shocks_empty_without_enough_history():
    assert market_shocks(_walk(5)) == []


# ── book replay ───────────────────────────────────────────────────────────────

def test_future_pnl_is_linear_in_the_spot_shock():
    shocks = [(100.0, 0.0), (-100.0, 0.0)]          # ±100bp, no vol move
    [vec] = simulate_pnl_by_position([_future(10)], 1.10, shocks)
    expected = 10 * 125_000 * 1.10 * 0.01
    assert vec[0] == pytest.approx(expected)
    assert vec[1] == pytest.approx(-expected)       # symmetric: futures have no gamma


def test_long_straddle_gains_on_a_vol_spike():
    book = [_option(1, 1.10, 0.08, "C", "c"), _option(1, 1.10, 0.08, "P", "p")]
    [c, p] = simulate_pnl_by_position(book, 1.10, [(0.0, 1.0)])   # +1 vol point
    assert c[0] > 0 and p[0] > 0                    # long vega on both legs


def test_portfolio_pnl_is_the_column_sum():
    book = [_future(10, pid="f"), _option(-1, 1.10, 0.08, "C", "o")]
    shocks = [(50.0, 0.2), (-50.0, -0.2), (0.0, 0.0)]
    by_pos = simulate_pnl_by_position(book, 1.10, shocks)
    pf = portfolio_pnl(by_pos)
    assert pf == pytest.approx([sum(col) for col in zip(*by_pos, strict=True)])
    assert len(pf) == len(shocks)


def test_portfolio_pnl_of_an_empty_book():
    assert portfolio_pnl([]) == []


def test_flat_scenario_is_zero_pnl():
    # No shock at all → the book reprices to its baseline (bar float noise).
    book = [_future(10, pid="f"), _option(1, 1.10, 0.08, "C", "o")]
    assert portfolio_pnl(simulate_pnl_by_position(book, 1.10, [(0.0, 0.0)]))[0] == pytest.approx(0.0, abs=1e-6)


def test_end_to_end_shape_from_closes_to_pnl_vector():
    shocks = market_shocks(_walk(300))
    book = [_option(5, 1.10, 0.08, "C", "c"), _option(5, 1.10, 0.08, "P", "p")]
    pnl = portfolio_pnl(simulate_pnl_by_position(book, 1.10, shocks))
    assert len(pnl) == len(shocks) > 200          # ~1y of daily bars → ~250 scenarios
    assert min(pnl) < 0 < max(pnl)                # a real two-sided distribution
