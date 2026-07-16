"""Unit tests for `_taylor_pnl_series` — the pure bucket-walk behind
`/portfolio/greek-pnl-history` (cumulative greek P&L, Taylor terms).

The endpoint's SQL uses Postgres-only constructs (DISTINCT ON, to_timestamp)
→ covered by the db_integration job; here we exercise the cumulation math.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.routers.portfolio_panel import _taylor_pnl_series

T0 = datetime(2026, 7, 1, 12, 0)
HOUR = timedelta(hours=1)


def _snap(delta=0.0, gamma=0.0, vega=0.0, theta=0.0, iv=None):
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "iv": iv}


def test_empty_book_yields_empty_series():
    assert _taylor_pnl_series({}, {}, []) == []


def test_series_starts_at_zero_and_cumulates_delta_gamma():
    buckets = [T0, T0 + HOUR, T0 + 2 * HOUR]
    forward = {T0: 1.10, T0 + HOUR: 1.11, T0 + 2 * HOUR: 1.13}
    # one leg, greeks frozen at the interval start: δ=1000 $/unit, Γ=200 $/unit²
    per_pos = {1: {b: _snap(delta=1000.0, gamma=200.0) for b in buckets}}
    out = _taylor_pnl_series(per_pos, forward, buckets)
    assert [o["timestamp"] for o in out] == [b.replace(tzinfo=UTC).isoformat() for b in buckets]
    assert out[0]["delta_pnl_usd"] == 0.0  # window start = baseline
    # bucket 2: 1000 × 0.01 = 10 ; bucket 3: + 1000 × 0.02 = 30 cumulative
    assert out[1]["delta_pnl_usd"] == pytest.approx(10.0)
    assert out[2]["delta_pnl_usd"] == pytest.approx(30.0)
    # gamma: ½·200·0.01² = 0.01 then + ½·200·0.02² = 0.05 cumulative
    assert out[2]["gamma_pnl_usd"] == pytest.approx(0.05)


def test_vega_uses_leg_iv_and_theta_accrues_days():
    buckets = [T0, T0 + 12 * HOUR]
    forward = {b: 1.10 for b in buckets}  # flat spot → no delta/gamma P&L
    per_pos = {
        1: {
            buckets[0]: _snap(vega=500.0, theta=-48.0, iv=5.0),
            buckets[1]: _snap(vega=500.0, theta=-48.0, iv=5.4),
        }
    }
    out = _taylor_pnl_series(per_pos, forward, buckets)
    assert out[1]["delta_pnl_usd"] == 0.0
    assert out[1]["vega_pnl_usd"] == pytest.approx(500.0 * 0.4)
    assert out[1]["theta_pnl_usd"] == pytest.approx(-48.0 * 0.5)  # half a day


def test_missing_forward_skips_spot_terms_but_not_theta():
    buckets = [T0, T0 + 24 * HOUR]
    forward = {T0: None, T0 + 24 * HOUR: None}
    per_pos = {1: {b: _snap(delta=1000.0, gamma=200.0, theta=-10.0) for b in buckets}}
    out = _taylor_pnl_series(per_pos, forward, buckets)
    assert out[1]["delta_pnl_usd"] == 0.0
    assert out[1]["gamma_pnl_usd"] == 0.0
    assert out[1]["theta_pnl_usd"] == pytest.approx(-10.0)


def test_positions_sum_per_bucket():
    buckets = [T0, T0 + HOUR]
    forward = {T0: 1.10, T0 + HOUR: 1.12}
    per_pos = {
        1: {b: _snap(delta=1000.0) for b in buckets},
        2: {b: _snap(delta=-400.0) for b in buckets},
    }
    out = _taylor_pnl_series(per_pos, forward, buckets)
    assert out[1]["delta_pnl_usd"] == pytest.approx((1000.0 - 400.0) * 0.02)
