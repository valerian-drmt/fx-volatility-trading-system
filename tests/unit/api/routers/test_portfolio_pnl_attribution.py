"""Unit tests for `_decompose_row` — the per-leg Taylor P&L attribution behind
`/portfolio/pnl-attribution`.

Covers the two correctness fixes:
  * gamma units — the stored gamma is $/pip (IB-live) or $/pip² (booked); with a
    raw-spot dS the ½Γ·dS² term must be scaled to Γ_$ or it collapses to ~$0 and
    the convexity P&L leaks into the residual;
  * futures foot — a linear future carries explicit 0 gamma/vega/theta so the
    residual is computed (was None) and δ·dS + residual reconciles to actual.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.routers.portfolio_panel import _decompose_row
from core.units import PIP_SIZE

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
DS = 0.008  # 80-pip spot move (1.150 → 1.158)


def _foots(out: dict[str, float | None]) -> None:
    """δ·dS + ½Γ·dS² + V·dσ + Θ·dt + residual must reconcile to actual."""
    parts = sum(
        float(out[k])
        for k in ("delta_pnl_usd", "gamma_pnl_usd", "vega_pnl_usd", "theta_pnl_usd", "residual_usd")
    )
    assert parts == pytest.approx(float(out["actual_pnl_usd"]))


def test_ib_gamma_usd_scaled_to_real_dollars_and_foots():
    # gamma_usd = $/pip. A long-gamma leg through an 80-pip move must show
    # material convexity P&L (0.5·(5000/PIP_SIZE)·0.008² = $1,600), not ~$0.
    out = _decompose_row(
        now=NOW, pnl_now=1600.0, pnl_then=0.0,
        spot_now=1.158, spot_then=1.150,
        iv_now=0.0, iv_then=0.0,
        delta=0.0, gamma=5000.0, vega=0.0, theta=0.0,
        t_then=NOW, gamma_scale=1.0 / PIP_SIZE,
    )
    assert out["gamma_pnl_usd"] == pytest.approx(1600.0)
    assert out["residual_usd"] == pytest.approx(0.0)
    _foots(out)


def test_unscaled_gamma_is_the_old_bug_near_zero():
    # gamma_scale=1.0 reproduces the pre-fix behaviour: the term is 1e4× too
    # small (~$0.16) — regression guard so the scale can't silently drop again.
    out = _decompose_row(
        now=NOW, pnl_now=0.0, pnl_then=0.0,
        spot_now=1.158, spot_then=1.150,
        iv_now=None, iv_then=None,
        delta=None, gamma=5000.0, vega=None, theta=None,
        t_then=NOW, gamma_scale=1.0,
    )
    assert abs(float(out["gamma_pnl_usd"])) < 1.0


def test_booked_gamma_per_pip2_scaled():
    # booked gamma is $/pip² → needs 1/PIP_SIZE². 0.5·(5.3/PIP_SIZE²)·0.008² ≈ $16,960.
    out = _decompose_row(
        now=NOW, pnl_now=0.0, pnl_then=0.0,
        spot_now=1.158, spot_then=1.150,
        iv_now=None, iv_then=None,
        delta=None, gamma=5.3, vega=None, theta=None,
        t_then=NOW, gamma_scale=1.0 / (PIP_SIZE * PIP_SIZE),
    )
    expected = 0.5 * (5.3 / (PIP_SIZE * PIP_SIZE)) * DS * DS
    assert out["gamma_pnl_usd"] == pytest.approx(expected, rel=1e-6)
    assert expected > 1000.0  # material, not ~$0


def test_future_foots_via_residual():
    # Linear future: delta = notional ($/price), gamma/vega/theta = 0. When
    # actual differs from δ·dS (basis / own-price), the residual must capture the
    # gap so the row reconciles — previously residual was None (blank) and the
    # gap footed nowhere, breaking the totals row.
    delta = -3_000_000.0
    dS = 1.15560 - 1.14787  # 0.00773 → δ·dS = -$23,190
    out = _decompose_row(
        now=NOW, pnl_now=-20000.0, pnl_then=0.0,
        spot_now=1.15560, spot_then=1.14787,
        iv_now=0.0, iv_then=0.0,
        delta=delta, gamma=0.0, vega=0.0, theta=0.0,
        t_then=NOW, gamma_scale=1.0,
    )
    assert out["delta_pnl_usd"] == pytest.approx(delta * dS, rel=1e-6)
    assert out["gamma_pnl_usd"] == 0.0
    assert out["residual_usd"] is not None
    _foots(out)


def test_no_t1_snapshot_suppresses_all_terms():
    # Unmeasurable window (no t-1 pnl) → every term None, nothing fabricated.
    out = _decompose_row(
        now=NOW, pnl_now=None, pnl_then=None,
        spot_now=1.158, spot_then=1.150,
        iv_now=1.0, iv_then=1.0,
        delta=1000.0, gamma=5000.0, vega=100.0, theta=-10.0,
        t_then=NOW, gamma_scale=1.0 / PIP_SIZE,
    )
    assert all(v is None for v in out.values())
