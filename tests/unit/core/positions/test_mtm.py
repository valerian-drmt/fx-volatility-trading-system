"""Unit tests for core.positions.mtm."""
from __future__ import annotations

import pytest

from core.positions.mtm import attribute_pnl, compute_mtm


def test_compute_mtm_long_premium_up():
    """Long premium 3000, mark 3500 → +500 gross."""
    r = compute_mtm(
        entry_premium_usd=3000.0, mark_value_usd=3500.0,
        entry_total_cost_usd=30.0, hedge_cost_cumul_usd=10.0,
        spot_now=1.0855, iv_now_pct=7.1,
    )
    assert r.pnl_gross_usd == pytest.approx(500.0)
    assert r.pnl_net_usd == pytest.approx(460.0)


def test_compute_mtm_short_position():
    """Short premium (received 3000) → entry_premium=-3000 ; mark=-2800 → +200."""
    r = compute_mtm(
        entry_premium_usd=-3000.0, mark_value_usd=-2800.0,
        entry_total_cost_usd=30.0, hedge_cost_cumul_usd=0.0,
        spot_now=1.0855, iv_now_pct=7.0,
    )
    assert r.pnl_gross_usd == pytest.approx(200.0)


def test_attribute_vega_pnl_only():
    """Pure IV move : 1 vol pt up × vega 847 = +847."""
    a = attribute_pnl(
        pnl_gross_usd=847.0,
        entry_vega_usd_per_volpt=847.0,
        entry_gamma_usd_per_pip2=0.0,
        entry_theta_usd_per_day=0.0,
        iv_entry_pct=7.0, iv_now_pct=8.0,
        spot_entry=1.0850, spot_now=1.0850,
        days_elapsed=0.0,
    )
    assert a.vega_usd == pytest.approx(847.0)
    assert a.gamma_usd == pytest.approx(0.0)
    assert a.theta_usd == pytest.approx(0.0)
    assert a.other_usd == pytest.approx(0.0)


def test_attribute_gamma_quadratic():
    """Spot up 100 pips, gamma 2 $/pip² → ½ × 2 × 100² = 10 000."""
    a = attribute_pnl(
        pnl_gross_usd=10_000.0,
        entry_vega_usd_per_volpt=0.0,
        entry_gamma_usd_per_pip2=2.0,
        entry_theta_usd_per_day=0.0,
        iv_entry_pct=7.0, iv_now_pct=7.0,
        spot_entry=1.0850, spot_now=1.0950,
        days_elapsed=0.0,
    )
    assert a.gamma_usd == pytest.approx(10_000.0)


def test_attribute_theta_linear():
    a = attribute_pnl(
        pnl_gross_usd=-100.0,
        entry_vega_usd_per_volpt=0.0,
        entry_gamma_usd_per_pip2=0.0,
        entry_theta_usd_per_day=-50.0,
        iv_entry_pct=7.0, iv_now_pct=7.0,
        spot_entry=1.0850, spot_now=1.0850,
        days_elapsed=2.0,
    )
    assert a.theta_usd == pytest.approx(-100.0)


def test_attribute_residual_to_other():
    """Total = 1000, attributed = 700 → other = 300."""
    a = attribute_pnl(
        pnl_gross_usd=1000.0,
        entry_vega_usd_per_volpt=500.0,
        entry_gamma_usd_per_pip2=0.0,
        entry_theta_usd_per_day=0.0,
        iv_entry_pct=7.0, iv_now_pct=8.4,  # 0.4 vol-pt → 700 (using vega 500)
        spot_entry=1.0850, spot_now=1.0850,
        days_elapsed=0.0,
    )
    assert a.vega_usd == pytest.approx(700.0)
    assert a.other_usd == pytest.approx(300.0)


def test_attribution_sums_to_total():
    """Invariant : vega + gamma + theta + other == pnl_gross."""
    a = attribute_pnl(
        pnl_gross_usd=1234.5,
        entry_vega_usd_per_volpt=200.0,
        entry_gamma_usd_per_pip2=1.5,
        entry_theta_usd_per_day=-25.0,
        iv_entry_pct=6.5, iv_now_pct=7.2,
        spot_entry=1.0800, spot_now=1.0875,
        days_elapsed=3.0,
    )
    assert a.total_attributed == pytest.approx(1234.5)
