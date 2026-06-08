"""Step 3 — pure-Python unit tests for trade_preview.py.

Covers : recommendation parser, structure builder, pricing reconciliation,
greeks signs, sizing formula, scenario decomposition, all 7 pre-submit checks.
"""
from __future__ import annotations

import pytest

from core.trade_preview import (
    DEFAULT_SCENARIOS,
    TEMPLATES,
    bs_greeks,
    bs_price,
    build_structure,
    compute_net_greeks,
    compute_sizing,
    parse_recommendation,
    price_structure,
    run_pre_submit_checks,
    simulate_scenarios,
)


def _mock_surface() -> dict:
    """Synthetic 6-tenor × 5-delta surface around spot=1.0850, σ=7%."""
    pillars = ["10dp", "25dp", "atm", "25dc", "10dc"]
    smile = [0.6, 0.2, 0.0, 0.1, 0.45]  # additive % vol
    base_atm_iv = [0.068, 0.069, 0.070, 0.0705, 0.071, 0.0715]
    spot = 1.0850
    surface: dict = {}
    for ti, t in enumerate(("1M", "2M", "3M", "4M", "5M", "6M")):
        node: dict = {}
        for di, d in enumerate(pillars):
            iv = base_atm_iv[ti] + smile[di] / 100.0
            # strike spread for delta pillars (rough)
            offset = (di - 2) * 0.005
            node[d] = {"iv": iv, "strike": spot + offset}
        surface[t] = node
    return surface


# ────────────────────────────────────────────────────────────────
# Recommendation parser
# ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rec,expected", [
    ("straddle_atm_3M", ("straddle_atm", "3M", None)),
    ("short_strangle_3M", ("short_strangle", "3M", None)),
    ("long_butterfly_25d_3M", ("long_butterfly_25d", "3M", None)),
    ("calendar_long_1M_3M", ("calendar_long", "1M", "3M")),
    ("calendar_short_2M_6M", ("calendar_short", "2M", "6M")),
])
def test_parse_recommendation(rec, expected):
    assert parse_recommendation(rec) == expected


def test_parse_recommendation_invalid_raises():
    with pytest.raises(ValueError):
        parse_recommendation("garbage")
    with pytest.raises(ValueError):
        parse_recommendation("")


# ────────────────────────────────────────────────────────────────
# Builder
# ────────────────────────────────────────────────────────────────


def test_build_structure_straddle_atm_2_legs():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    assert len(s.legs) == 2
    assert {leg.contract_type for leg in s.legs} == {"call", "put"}
    assert all(leg.tenor == "3M" for leg in s.legs)
    assert all(leg.dte == 90 for leg in s.legs)
    assert s.legs[0].strike == s.legs[1].strike  # both ATM


def test_build_structure_calendar_uses_two_tenors():
    s = build_structure("calendar_long", "1M", "3M", _mock_surface())
    tenors = {leg.tenor for leg in s.legs}
    assert tenors == {"1M", "3M"}


def test_build_structure_butterfly_3_legs():
    s = build_structure("long_butterfly_25d", "3M", None, _mock_surface())
    assert len(s.legs) == 3
    qty_factors = [leg.qty_factor for leg in s.legs]
    assert qty_factors == [1, 2, 1]


def test_build_structure_unknown_raises():
    with pytest.raises(ValueError):
        build_structure("totally_made_up", "3M", None, _mock_surface())


def test_templates_match_seed_count():
    # 6 spec structures + 4 off-strategy variants (short_straddle, long_strangle,
    # future_buy/sell) + 4 vanilla (call/put × buy/sell) = 14 exposed in the UI.
    assert len(TEMPLATES) == 14


def test_short_straddle_greeks_inverted():
    s = build_structure("short_straddle_atm", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.vega_usd_per_volpt < 0
    assert g.theta_usd_per_day > 0


def test_long_strangle_greeks_signs():
    s = build_structure("long_strangle_25d", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.vega_usd_per_volpt > 0
    assert g.theta_usd_per_day < 0


def test_future_buy_delta_plus_one_no_other_greeks():
    s = build_structure("future_buy", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.delta_unhedged == 1.0
    assert g.vega_usd_per_volpt == 0.0
    assert g.gamma_usd_per_pip2 == 0.0
    assert g.theta_usd_per_day == 0.0


def test_future_sell_delta_minus_one():
    s = build_structure("future_sell", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.delta_unhedged == -1.0


def test_future_no_strike_no_iv():
    s = build_structure("future_buy", "3M", None, _mock_surface())
    leg = s.legs[0]
    assert leg.contract_type == "future"
    assert leg.strike is None
    assert leg.entry_iv_pct is None


def test_vanilla_call_buy_greeks_signs():
    s = build_structure("vanilla_call", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.delta_unhedged > 0
    assert g.vega_usd_per_volpt > 0
    assert g.gamma_usd_per_pip2 > 0
    assert g.theta_usd_per_day < 0


def test_short_vanilla_put_signs_inverted():
    s = build_structure("short_vanilla_put", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.vega_usd_per_volpt < 0
    assert g.theta_usd_per_day > 0


def test_delta_pillar_override_applies_on_single_leg():
    s = build_structure(
        "vanilla_call", "3M", None, _mock_surface(),
        delta_pillar_override="25dc",
    )
    # The leg should now reference 25dc strike, not atm.
    expected = _mock_surface()["3M"]["25dc"]["strike"]
    assert s.legs[0].strike == pytest.approx(expected)


def test_strike_override_applies_on_single_leg():
    s = build_structure(
        "vanilla_call", "3M", None, _mock_surface(),
        strike_override=1.1234,
    )
    assert s.legs[0].strike == pytest.approx(1.1234)


def test_delta_pillar_override_mirrors_on_straddle():
    """Straddle (template both ATM) + override 25dc → call=25dc, put=25dp."""
    s = build_structure(
        "straddle_atm", "3M", None, _mock_surface(),
        delta_pillar_override="25dc",
    )
    # Find call and put legs
    call_leg = next(leg for leg in s.legs if leg.contract_type == "call")
    put_leg = next(leg for leg in s.legs if leg.contract_type == "put")
    surface = _mock_surface()
    assert call_leg.strike == pytest.approx(surface["3M"]["25dc"]["strike"])
    assert put_leg.strike == pytest.approx(surface["3M"]["25dp"]["strike"])


def test_delta_pillar_override_mirrors_on_strangle():
    """Strangle override 10dp → call=10dc, put=10dp (works whether user picked dc or dp)."""
    s = build_structure(
        "short_strangle", "3M", None, _mock_surface(),
        delta_pillar_override="10dp",
    )
    call_leg = next(leg for leg in s.legs if leg.contract_type == "call")
    put_leg = next(leg for leg in s.legs if leg.contract_type == "put")
    surface = _mock_surface()
    assert call_leg.strike == pytest.approx(surface["3M"]["10dc"]["strike"])
    assert put_leg.strike == pytest.approx(surface["3M"]["10dp"]["strike"])


def test_butterfly_body_stays_atm_on_override():
    """Butterfly : wings move with override, body (overridable=False) stays ATM."""
    s = build_structure(
        "long_butterfly_25d", "3M", None, _mock_surface(),
        delta_pillar_override="25dc",
    )
    # leg 0 = wing call, leg 1 = body (ATM, x2 SELL), leg 2 = wing put.
    surface = _mock_surface()
    assert s.legs[0].strike == pytest.approx(surface["3M"]["25dc"]["strike"])
    assert s.legs[1].strike == pytest.approx(surface["3M"]["atm"]["strike"])  # body unchanged
    assert s.legs[2].strike == pytest.approx(surface["3M"]["25dp"]["strike"])


# ────────────────────────────────────────────────────────────────
# Black-Scholes / Greeks primitives
# ────────────────────────────────────────────────────────────────


def test_bs_call_put_parity_atm():
    F, K, T, sigma = 1.0850, 1.0850, 90 / 365, 0.07
    c = bs_price(F, K, T, sigma, "call")
    p = bs_price(F, K, T, sigma, "put")
    # Black-76 zero rate : C - P = F - K = 0 at ATM
    assert abs(c - p) < 1e-6


def test_bs_greeks_call_delta_in_0_1():
    g = bs_greeks(1.085, 1.085, 0.25, 0.07, "call")
    assert 0.0 < g["delta"] < 1.0
    assert g["gamma"] > 0
    assert g["vega"] > 0
    assert g["theta"] < 0


def test_bs_greeks_put_delta_negative():
    g = bs_greeks(1.085, 1.085, 0.25, 0.07, "put")
    assert -1.0 < g["delta"] < 0.0


# ────────────────────────────────────────────────────────────────
# Pricing + greeks aggregation
# ────────────────────────────────────────────────────────────────


def test_long_straddle_premium_positive():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    p = price_structure(s, _mock_surface())
    assert p.total_premium_usd > 0


def test_short_strangle_premium_negative():
    s = build_structure("short_strangle", "3M", None, _mock_surface())
    p = price_structure(s, _mock_surface())
    assert p.total_premium_usd < 0


def test_long_straddle_greeks_signs():
    """Long straddle : vega+, gamma+, theta- (canonical)."""
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.vega_usd_per_volpt > 0
    assert g.gamma_usd_per_pip2 > 0
    assert g.theta_usd_per_day < 0


def test_short_strangle_greeks_signs_inverted():
    s = build_structure("short_strangle", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    assert g.vega_usd_per_volpt < 0
    assert g.gamma_usd_per_pip2 < 0
    assert g.theta_usd_per_day > 0


# ────────────────────────────────────────────────────────────────
# Scenarios
# ────────────────────────────────────────────────────────────────


def test_scenario_count_matches_default_grid():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    scenarios = simulate_scenarios(s, _mock_surface(), g)
    assert len(scenarios) == len(DEFAULT_SCENARIOS)
    labels = {sc["label"] for sc in scenarios}
    assert labels == {"favorable", "neutral", "adverse"}


def test_scenario_total_pnl_decomposition_reconciles():
    """gamma_theta + vega ≈ total (within 1% tolerance for Taylor approximation)."""
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    for sc in simulate_scenarios(s, _mock_surface(), g):
        decomposed = sc["pnl_gamma_theta_usd"] + sc["pnl_vega_usd"]
        assert abs(decomposed - sc["pnl_total_usd"]) < 0.05


# ────────────────────────────────────────────────────────────────
# Sizing
# ────────────────────────────────────────────────────────────────


def test_sizing_z_factor_applied():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    p = price_structure(s, _mock_surface())
    sz = compute_sizing(
        z_score=2.25, structure=s, total_premium=p.total_premium_usd,
        book_total_vega_usd=0.0, book_vega_neutral_threshold=2000.0,
        base_qty=10, threshold_min=1.5, max_z_multiplier=2.0, book_alpha=0.3,
        regime=None,
    )
    # z_factor = min(2.25/1.5, 2.0) = 1.5 ; book_penalty=1 ; event=1 ; regime=1 → 15
    assert sz.final_qty_per_leg == 15
    assert sz.multipliers["z_score_factor"] == pytest.approx(1.5)


def test_sizing_book_penalty_when_same_sign_long_vega():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())  # vega+
    p = price_structure(s, _mock_surface())
    sz = compute_sizing(
        z_score=1.5, structure=s, total_premium=p.total_premium_usd,
        book_total_vega_usd=4000.0,                      # already long vega
        book_vega_neutral_threshold=2000.0,
        base_qty=10, threshold_min=1.5, max_z_multiplier=2.0, book_alpha=0.3,
        regime=None,
    )
    assert sz.multipliers["book_penalty"] < 1.0


def test_sizing_pre_event_zeroes_out():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    p = price_structure(s, _mock_surface())
    sz = compute_sizing(
        z_score=2.0, structure=s, total_premium=p.total_premium_usd,
        book_total_vega_usd=0.0, book_vega_neutral_threshold=2000.0,
        base_qty=10, threshold_min=1.5, max_z_multiplier=2.0, book_alpha=0.3,
        regime={"regime": "pre_event"},
    )
    assert sz.multipliers["regime_multiplier"] == 0.0
    assert sz.final_qty_per_leg == 0


def test_sizing_qty_override():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    p = price_structure(s, _mock_surface())
    sz = compute_sizing(
        z_score=2.0, structure=s, total_premium=p.total_premium_usd,
        book_total_vega_usd=0.0, book_vega_neutral_threshold=2000.0,
        base_qty=10, threshold_min=1.5, max_z_multiplier=2.0, book_alpha=0.3,
        regime=None, qty_override=42,
    )
    assert sz.final_qty_per_leg == 42


# ────────────────────────────────────────────────────────────────
# Pre-submit checks (the 7)
# ────────────────────────────────────────────────────────────────


def _ok_args() -> dict:
    return {
        "regime": {"regime": "calm"},
        "armed_z": 2.0, "current_z": 2.0, "threshold_min": 1.5,
        "max_loss_usd": 500.0, "capital_total_usd": 100_000.0, "max_loss_pct": 2.0,
        "book_total_vega_usd": 0.0, "structure_vega_usd": 200.0, "max_book_vega_usd": 5000.0,
        "surface_age_seconds": 30.0, "max_iv_age_s": 120,
        "has_arb_violation": False, "min_quoted_size": 50, "min_liquidity": 10,
    }


def test_all_checks_pass_baseline():
    checks = run_pre_submit_checks(**_ok_args())
    assert all(c.passed for c in checks)
    assert {c.name for c in checks} == {
        "regime_not_pre_event", "signal_still_actionable", "max_loss_under_capital_limit",
        "vega_under_book_limit", "iv_data_fresh", "no_arb_violation_on_legs", "minimum_liquidity",
    }


def test_check_pre_event_blocks():
    args = _ok_args()
    args["regime"] = {"regime": "pre_event"}
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "regime_not_pre_event").passed is False


def test_check_signal_flipped_blocks():
    args = _ok_args()
    args["armed_z"] = 2.0
    args["current_z"] = -0.5
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "signal_still_actionable").passed is False


def test_check_signal_too_weak_blocks():
    args = _ok_args()
    args["current_z"] = 0.5  # below threshold * 0.7 = 1.05
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "signal_still_actionable").passed is False


def test_check_max_loss_blocks_oversized():
    args = _ok_args()
    args["max_loss_usd"] = 5000.0  # 5% of capital
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "max_loss_under_capital_limit").passed is False


def test_check_vega_book_limit_blocks():
    args = _ok_args()
    args["book_total_vega_usd"] = 4900.0
    args["structure_vega_usd"] = 200.0           # post = 5100 > limit 5000
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "vega_under_book_limit").passed is False


def test_check_iv_stale_blocks():
    args = _ok_args()
    args["surface_age_seconds"] = 200.0
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "iv_data_fresh").passed is False


def test_check_arb_violation_blocks():
    args = _ok_args()
    args["has_arb_violation"] = True
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "no_arb_violation_on_legs").passed is False


def test_check_liquidity_blocks():
    args = _ok_args()
    args["min_quoted_size"] = 5
    checks = run_pre_submit_checks(**args)
    assert next(c for c in checks if c.name == "minimum_liquidity").passed is False
