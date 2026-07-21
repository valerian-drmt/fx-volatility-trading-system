"""Golden tests locking the USD-at-notional units convention (core.units).

Reference values are computed independently in this file (Black-76, zero
rates, via math.erf) — NOT through the production pricers — so a scale
regression anywhere in the chain (preview → entry snapshot → monitor →
exit rules) fails loudly.

Canonical case : ATM straddle, spot = K = 1.10, T = 30/365, sigma = 8 %,
qty = 1 (per-structure-unit numbers).
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from core.positions.exit_rules import PositionContext, StopLossVegaRule
from core.positions.position_pricing import LegSpec, price_position
from core.trade_preview import (
    build_structure,
    compute_legs_greeks,
    compute_net_greeks,
    compute_pnl_grid,
    price_structure,
)
from core.units import EUR_FOP_MULTIPLIER, PIP_SIZE, VOLPT

SPOT = 1.10
K = 1.10
T = 30.0 / 365.0
SIGMA = 0.08


# ── independent Black-76 reference (zero rates) ─────────────────────────

def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(F: float, K_: float, T_: float, s: float) -> float:
    return (math.log(F / K_) + 0.5 * s * s * T_) / (s * math.sqrt(T_))


def _ref_call(F: float, K_: float, T_: float, s: float) -> float:
    d1 = _d1(F, K_, T_, s)
    d2 = d1 - s * math.sqrt(T_)
    return F * _N(d1) - K_ * _N(d2)


def _ref_put(F: float, K_: float, T_: float, s: float) -> float:
    d1 = _d1(F, K_, T_, s)
    d2 = d1 - s * math.sqrt(T_)
    return K_ * _N(-d2) - F * _N(-d1)


def _ref_vega(F: float, K_: float, T_: float, s: float) -> float:
    return F * _phi(_d1(F, K_, T_, s)) * math.sqrt(T_)


def _ref_gamma(F: float, K_: float, T_: float, s: float) -> float:
    return _phi(_d1(F, K_, T_, s)) / (F * s * math.sqrt(T_))


# ── fixtures ────────────────────────────────────────────────────────────

def _preview_surface() -> dict:
    """Surface in the preview format {tenor: {pillar: {iv, strike}}}."""
    node = {p: {"iv": SIGMA, "strike": SPOT} for p in ("10dp", "25dp", "atm", "25dc", "10dc")}
    return {"1M": node}


def _scanner_surface() -> dict:
    """Surface in the scanner format used by core.pricing.bs.interpolate_iv."""
    return {"1M": {"sigma_ATM_pct": SIGMA * 100.0, "strike_atm": SPOT}}


def _straddle():
    return build_structure("straddle_atm", "1M", None, _preview_surface())


# ── premium ─────────────────────────────────────────────────────────────

def test_straddle_premium_is_usd_at_notional():
    p = price_structure(_straddle(), _preview_surface())
    expected = (_ref_call(SPOT, K, T, SIGMA) + _ref_put(SPOT, K, T, SIGMA)) * EUR_FOP_MULTIPLIER
    assert p.total_premium_usd == pytest.approx(expected, rel=1e-6)
    # ATM 8 %, 30d, €125k notional straddle costs ~ $2.5k, not ~ $0.02
    # (the old price-points scale) — hard bound to make the scale explicit.
    assert 1_000.0 < p.total_premium_usd < 10_000.0
    # per-leg prices are USD per contract too
    assert p.leg_prices_usd[0] == pytest.approx(
        _ref_call(SPOT, K, T, SIGMA) * EUR_FOP_MULTIPLIER, rel=1e-6
    )
    assert p.max_loss_usd == pytest.approx(p.total_premium_usd, rel=1e-9)


# ── greeks ──────────────────────────────────────────────────────────────

def test_straddle_vega_usd_per_volpt():
    g = compute_net_greeks(_straddle(), _preview_surface())
    expected = 2.0 * _ref_vega(SPOT, K, T, SIGMA) * VOLPT * EUR_FOP_MULTIPLIER
    assert g.vega_usd_per_volpt == pytest.approx(expected, rel=1e-6)


def test_straddle_gamma_usd_per_pip2():
    g = compute_net_greeks(_straddle(), _preview_surface())
    expected = 2.0 * _ref_gamma(SPOT, K, T, SIGMA) * PIP_SIZE ** 2 * EUR_FOP_MULTIPLIER
    assert g.gamma_usd_per_pip2 == pytest.approx(expected, rel=1e-4)


def test_legs_greeks_sum_to_net():
    s = _straddle()
    g = compute_net_greeks(s, _preview_surface())
    rows = compute_legs_greeks(s, _preview_surface())
    assert sum(r["vega"] for r in rows) == pytest.approx(g.vega_usd_per_volpt, abs=0.02)
    assert sum(r["theta"] for r in rows) == pytest.approx(g.theta_usd_per_day, abs=0.02)


# ── gamma P&L : shocks in pips ──────────────────────────────────────────

def test_pnl_grid_gamma_cell_uses_pips():
    s = _straddle()
    g = compute_net_greeks(s, _preview_surface())
    grid = compute_pnl_grid(s, _preview_surface(), g)
    row = next(r for r in grid["rows"] if r["ds_pct"] == 2.0)
    cell = next(c for c in row["cells"] if c["div_volpts"] == 0.0)
    ds_pips = SPOT * 0.02 / PIP_SIZE            # +2 % of 1.10 = 220 pips
    expected = 0.5 * g.gamma_usd_per_pip2 * ds_pips ** 2
    assert cell["pnl_usd"] == pytest.approx(expected, abs=0.51)  # cell rounds to $1
    # A 2 % spot move on a 30d ATM €125k straddle is a ~$1k gamma P&L,
    # not cents (the old raw-spot-shock bug understated it ~10^4x).
    assert expected > 500.0


# ── cross-module scale agreement : preview premium == monitor mark ─────

def test_preview_premium_equals_position_mark_at_entry():
    now = datetime(2026, 7, 1, tzinfo=UTC)
    p = price_structure(_straddle(), _preview_surface())
    legs = [
        LegSpec(leg_idx=0, contract_type="call", strike=K,
                expiry=(now + timedelta(days=30)).date(), tenor="1M",
                side="BUY", qty=1),
        LegSpec(leg_idx=1, contract_type="put", strike=K,
                expiry=(now + timedelta(days=30)).date(), tenor="1M",
                side="BUY", qty=1),
    ]
    mark = price_position(legs=legs, surface=_scanner_surface(), spot=SPOT, now=now)
    assert mark.n_surface_missing == 0
    # Same legs, same surface, same spot → the entry mark must equal the
    # preview premium (tolerance for the IV-interpolation path).
    assert mark.mark_value_usd == pytest.approx(p.total_premium_usd, rel=1e-6)
    # And the greeks agree across the two modules.
    g = compute_net_greeks(_straddle(), _preview_surface())
    assert mark.total_vega_usd_per_volpt == pytest.approx(g.vega_usd_per_volpt, rel=1e-6)
    assert mark.total_gamma_usd_per_pip2 == pytest.approx(g.gamma_usd_per_pip2, rel=1e-4)


def test_position_mark_micro_multiplier():
    now = datetime(2026, 7, 1, tzinfo=UTC)
    legs = [LegSpec(leg_idx=0, contract_type="call", strike=K,
                    expiry=(now + timedelta(days=30)).date(), tenor="1M",
                    side="BUY", qty=1)]
    full = price_position(legs=legs, surface=_scanner_surface(), spot=SPOT, now=now)
    micro = price_position(
        legs=legs, surface=_scanner_surface(), spot=SPOT, now=now,
        contract_multiplier=12_500.0,
    )
    assert micro.mark_value_usd == pytest.approx(full.mark_value_usd / 10.0, rel=1e-9)
    # delta is contract-equivalent : NOT scaled by the multiplier
    assert micro.total_delta == pytest.approx(full.total_delta, rel=1e-12)


# ── the vega stop-loss can actually fire ────────────────────────────────

def test_stop_loss_vega_rule_fires_on_consistent_units():
    g = compute_net_greeks(_straddle(), _preview_surface())
    vega = g.vega_usd_per_volpt
    assert vega > 0
    ctx = PositionContext(
        position_id=1, triggering_pc=1, entry_z_score=2.0,
        entry_vega_usd_per_volpt=vega, dte_at_entry=30, days_remaining=25,
    )
    rule = StopLossVegaRule()  # threshold : loss < -3 × |vega|
    assert rule.evaluate(ctx, -3.1 * vega).triggered is True
    assert rule.evaluate(ctx, -2.9 * vega).triggered is False


# ── single source of truth ──────────────────────────────────────────────

def test_units_constants_are_single_sourced():
    from core import trade_preview, units
    from core.positions import mtm, position_pricing

    assert trade_preview.EUR_FOP_MULTIPLIER is units.EUR_FOP_MULTIPLIER
    assert trade_preview.FUTURE_MULTIPLIERS is units.FUTURE_MULTIPLIERS
    assert mtm.PIP_SIZE == units.PIP_SIZE
    assert position_pricing.PIP_SIZE == units.PIP_SIZE
    assert position_pricing.VOLPT == units.VOLPT
