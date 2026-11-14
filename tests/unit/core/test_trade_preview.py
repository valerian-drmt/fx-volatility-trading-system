"""Step 3 — pure-Python unit tests for trade_preview.py.

Covers : structure builder, pricing reconciliation, greeks signs, sizing
formula, scenario decomposition, all 7 pre-submit checks.
"""
from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

import pytest

from core.trade_preview import (
    DEFAULT_SCENARIOS,
    TEMPLATES,
    bs_greeks,
    bs_price,
    classify_legs,
    compute_net_greeks,
    compute_sizing,
    price_structure,
    run_pre_submit_checks,
    simulate_scenarios,
)
from core.trade_preview import (
    build_from_legs as _build_from_legs,
)
from core.trade_preview import (
    build_structure as _build_structure,
)

# core takes the clock from the caller (#332); the tests pin one.
AS_OF = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
build_structure = partial(_build_structure, as_of=AS_OF)
build_from_legs = partial(_build_from_legs, as_of=AS_OF)


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
# Builder
# ────────────────────────────────────────────────────────────────


def test_build_structure_straddle_atm_2_legs():
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    assert len(s.legs) == 2
    assert {leg.contract_type for leg in s.legs} == {"call", "put"}
    assert all(leg.tenor == "3M" for leg in s.legs)
    # priced to the listed February option (2027-02-05), not to "90 days"
    assert all((leg.dte, leg.expiry) == (87, "2027-02-05") for leg in s.legs)
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


def test_future_buy_full_delta_usd_no_other_greeks():
    # 6E (full size) — delta_usd = +1 × 125_000 × spot. Mock spot = 1.085 →
    # delta_usd = 135_625. Vega / gamma / theta stay 0 for a future.
    s = build_structure("future_buy", "3M", None, _mock_surface())
    assert s.future_contract_size == "full"
    g = compute_net_greeks(s, _mock_surface())
    assert g.delta_unhedged == pytest.approx(125_000 * 1.085)
    assert g.vega_usd_per_volpt == 0.0
    assert g.gamma_usd_per_pip2 == 0.0
    assert g.theta_usd_per_day == 0.0


def test_future_buy_micro_delta_usd_one_tenth():
    # M6E (micro) — delta_usd = +1 × 12_500 × spot. Should be exactly
    # 1/10 of the full-size delta.
    s = build_structure(
        "future_buy", "3M", None, _mock_surface(),
        future_contract_size="micro",
    )
    assert s.future_contract_size == "micro"
    g = compute_net_greeks(s, _mock_surface())
    assert g.delta_unhedged == pytest.approx(12_500 * 1.085)


def test_future_sell_delta_negative_usd():
    s = build_structure("future_sell", "3M", None, _mock_surface())
    g = compute_net_greeks(s, _mock_surface())
    # Sell 6E full size : delta_usd = -125_000 × spot.
    assert g.delta_unhedged == pytest.approx(-125_000 * 1.085)


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
        "butterfly_arb_free": True, "calendar_arb_free": True,
    }


def _check(checks: list, name: str):
    return next(c for c in checks if c.name == name)


def test_all_checks_baseline():
    checks = run_pre_submit_checks(**_ok_args())
    assert {c.name for c in checks} == {
        "regime_not_pre_event", "signal_still_actionable",
        "no_arb_violation_on_legs", "minimum_liquidity",
    }
    assert not any(c.blocks for c in checks)
    # no limit computed by the code is a check any more (owner rule)
    assert "vega_under_book_limit" not in {c.name for c in checks}


def test_check_pre_event_blocks():
    args = _ok_args()
    args["regime"] = {"regime": "pre_event"}
    c = _check(run_pre_submit_checks(**args), "regime_not_pre_event")
    assert c.passed is False and c.blocks


def test_check_signal_flipped_blocks():
    args = _ok_args()
    args["armed_z"] = 2.0
    args["current_z"] = -0.5
    assert _check(run_pre_submit_checks(**args), "signal_still_actionable").blocks


def test_check_signal_too_weak_blocks():
    args = _ok_args()
    args["current_z"] = 0.5  # below threshold * 0.7 = 1.05
    assert _check(run_pre_submit_checks(**args), "signal_still_actionable").blocks


def test_the_arbitrage_check_reads_the_surface_and_informs():
    """#483: it was the constant ``has_arb_violation=False``. It now reports
    the flags of the surface's own fit, and does not stop an order: the
    calendar flag is raised on about a third of prod surfaces."""
    args = _ok_args()
    args["calendar_arb_free"] = False
    c = _check(run_pre_submit_checks(**args), "no_arb_violation_on_legs")
    assert c.passed is False and not c.blocks
    assert c.details == {"butterfly_arb_free": True, "calendar_arb_free": False}


def test_a_check_that_cannot_be_evaluated_is_not_passed():
    args = _ok_args()
    args["butterfly_arb_free"] = args["calendar_arb_free"] = None
    checks = run_pre_submit_checks(**args)
    assert _check(checks, "no_arb_violation_on_legs").passed is None
    # no quoted size reaches the preview: liquidity was the constant 99
    liq = _check(checks, "minimum_liquidity")
    assert liq.passed is None and not liq.blocks


def test_the_premium_is_the_premium_of_the_whole_order():
    """#483: on a manual ticket base == final, and the premium was scaled by
    final / base: a 22-lot showed, and was checked on, one contract's premium
    (prod preview tp_1b093610a10f: 769.32 for 22 puts)."""
    s = build_structure("straddle_atm", "3M", None, _mock_surface())
    sizing = compute_sizing(
        z_score=1.5, structure=s, total_premium=769.32, book_total_vega_usd=0.0,
        book_vega_neutral_threshold=2000.0, base_qty=22, threshold_min=1.5,
        max_z_multiplier=1.0, book_alpha=0.0, regime=None, qty_override=22,
    )
    assert sizing.final_qty_per_leg == 22
    assert sizing.final_premium_usd == pytest.approx(22 * 769.32)


def test_an_option_is_priced_to_the_listed_expiry_of_its_month():
    from datetime import date

    from core.execution.contract_builder import listed_option_expiry
    from core.trade_preview import priced_expiry

    # the three expiries prod holds
    assert listed_option_expiry("2026-12-20") == date(2026, 12, 4)
    assert listed_option_expiry(date(2027, 2, 12)) == date(2027, 2, 5)
    assert listed_option_expiry("202703") == date(2027, 3, 5)
    today = date(2026, 11, 13)
    assert priced_expiry(date(2027, 2, 11), today, is_option=True) == (date(2027, 2, 5), 84)
    # the month's option has expired: the target is kept
    assert priced_expiry(date(2026, 11, 20), today, is_option=True) == (date(2026, 11, 20), 7)
    # a future is not an option: its date is the tenor's
    assert priced_expiry(date(2027, 2, 11), today, is_option=False) == (date(2027, 2, 11), 90)


# ────────────────────────────────────────────────────────────────
# Free-legs builder (G-trade.preview) — products/delta/tenor/side composed
# freely; no template, no imposed structure.
# ────────────────────────────────────────────────────────────────


def test_build_from_legs_empty_raises():
    with pytest.raises(ValueError, match="at least one leg"):
        build_from_legs([], _mock_surface())


def test_build_from_legs_resolves_strike_and_iv_from_surface():
    s = build_from_legs(
        [{"contract_type": "call", "side": "BUY", "tenor": "3M", "delta_pillar": "25dc"}],
        _mock_surface(),
    )
    assert s.type == "custom"
    assert len(s.legs) == 1
    leg = s.legs[0]
    assert leg.strike is not None and leg.entry_iv_pct is not None
    assert leg.side == "BUY" and leg.qty_factor == 1


def test_build_from_legs_strike_override_wins():
    s = build_from_legs(
        [{"contract_type": "put", "side": "SELL", "tenor": "2M", "strike": 1.10}],
        _mock_surface(),
    )
    assert s.legs[0].strike == 1.10


@pytest.mark.parametrize(
    "spec, msg",
    [
        ({"contract_type": "swap", "side": "BUY", "tenor": "3M"}, "contract_type"),
        ({"contract_type": "call", "side": "HOLD", "tenor": "3M"}, "side"),
        ({"contract_type": "call", "side": "BUY", "tenor": "7Y"}, "tenor"),
        ({"contract_type": "call", "side": "BUY", "tenor": "3M", "delta_pillar": "50d"}, "delta_pillar"),
        ({"contract_type": "call", "side": "BUY", "tenor": "3M", "qty_factor": 0}, "qty_factor"),
    ],
)
def test_build_from_legs_rejects_bad_input(spec, msg):
    with pytest.raises(ValueError, match=msg):
        build_from_legs([spec], _mock_surface())


def test_build_from_legs_vega_sign_and_hedge_flag():
    # Two bought options ⇒ net long vega, delta-hedgeable.
    s = build_from_legs(
        [
            {"contract_type": "call", "side": "BUY", "tenor": "3M", "delta_pillar": "25dc"},
            {"contract_type": "put", "side": "BUY", "tenor": "3M", "delta_pillar": "25dp"},
        ],
        _mock_surface(),
    )
    assert s.vega_sign == "positive"
    assert s.requires_delta_hedge is True


def test_build_from_legs_future_only_not_hedged_and_size():
    s = build_from_legs(
        [{"contract_type": "future", "side": "BUY", "tenor": "3M", "future_contract_size": "micro"}],
        _mock_surface(),
    )
    assert s.requires_delta_hedge is False  # the future *is* the delta
    assert s.future_contract_size == "micro"
    assert s.vega_sign == "neutral"


def test_build_from_legs_snaps_interp_tenor_to_listed():
    # Surface with 6M interpolated (no contract) and 3M/5M/9M listed. A 6M leg
    # must trade the nearest LISTED tenor (5M) and keep 6M as requested_tenor.
    pillars = ["10dp", "25dp", "atm", "25dc", "10dc"]
    def row(iv, src):
        return {p: {"iv": iv, "strike": 1.10, "source": src} for p in pillars}
    surface = {
        "3M": row(0.070, "listed"), "5M": row(0.073, "listed"),
        "6M": row(0.074, "interp"), "9M": row(0.076, "listed"),
    }
    s = build_from_legs(
        [{"contract_type": "call", "side": "BUY", "tenor": "6M", "delta_pillar": "atm"}],
        surface,
    )
    leg = s.legs[0]
    assert leg.snapped is True
    assert leg.requested_tenor == "6M"
    assert leg.tenor == "5M"            # nearest listed (150d) to 6M (180d)
    assert leg.entry_iv_pct == pytest.approx(7.3)  # priced off the listed 5M smile


def test_build_from_legs_no_snap_without_source_flags():
    # Synthetic surface (no source flags) → every present tenor is "listed" → no snap.
    s = build_from_legs(
        [{"contract_type": "call", "side": "BUY", "tenor": "3M", "delta_pillar": "atm"}],
        _mock_surface(),
    )
    assert s.legs[0].snapped is False and s.legs[0].requested_tenor is None


def test_build_from_legs_pricing_matches_template_equivalent():
    """A hand-composed ATM straddle must price/greek identically to the template."""
    surface = _mock_surface()
    custom = build_from_legs(
        [
            {"contract_type": "call", "side": "BUY", "tenor": "3M", "delta_pillar": "atm"},
            {"contract_type": "put", "side": "BUY", "tenor": "3M", "delta_pillar": "atm"},
        ],
        surface,
    )
    tpl = build_structure("straddle_atm", "3M", None, surface) if "straddle_atm" in TEMPLATES else None
    if tpl is not None:
        assert price_structure(custom, surface).total_premium_usd == pytest.approx(
            price_structure(tpl, surface).total_premium_usd
        )


@pytest.mark.parametrize(
    "legs, expected",
    [
        ([("call", "BUY", "atm")], "long call"),
        ([("put", "SELL", "25dp")], "short put"),
        ([("call", "BUY", "atm"), ("put", "BUY", "atm")], "long straddle"),
        ([("call", "BUY", "25dc"), ("put", "BUY", "25dp")], "long strangle"),
        ([("call", "BUY", "25dc"), ("put", "SELL", "25dp")], "risk reversal"),
        # vertical spreads : same type, opposite sides, one tenor
        ([("call", "BUY", "atm"), ("call", "SELL", "25dc")], "call spread"),
        ([("put", "BUY", "atm"), ("put", "SELL", "25dp")], "put spread"),
    ],
)
def test_classify_legs_names_common_shapes(legs, expected):
    specs = [{"contract_type": ct, "side": sd, "tenor": "3M", "delta_pillar": p} for ct, sd, p in legs]
    s = build_from_legs(specs, _mock_surface())
    assert classify_legs(s.legs) == expected


def test_classify_strangle_vs_straddle_and_delta_bucket():
    from core.trade_preview import Leg, _strangle_delta_bucket

    # different strikes → strangle ; same ATM strike → straddle
    strangle = build_from_legs(
        [
            {"contract_type": "put", "side": "BUY", "tenor": "1M", "delta_pillar": "25dp"},
            {"contract_type": "call", "side": "BUY", "tenor": "1M", "delta_pillar": "25dc"},
        ],
        _mock_surface(),
    )
    assert strangle.product_label.startswith("long strangle")
    straddle = build_from_legs(
        [
            {"contract_type": "call", "side": "BUY", "tenor": "1M", "delta_pillar": "atm"},
            {"contract_type": "put", "side": "BUY", "tenor": "1M", "delta_pillar": "atm"},
        ],
        _mock_surface(),
    )
    assert straddle.product_label == "long straddle"

    # bucket from real deltas : wide OTM legs → ~10Δ ; no spot → no bucket
    def _leg(ct: str, k: float) -> Leg:
        return Leg(leg_idx=0, contract_type=ct, tenor="1M", expiry="", dte=30,
                   strike=k, qty_factor=1, side="BUY", entry_iv_pct=7.0)
    assert _strangle_delta_bucket([_leg("put", 1.00), _leg("call", 1.20)], 1.10) == "10d"
    assert _strangle_delta_bucket([_leg("put", 1.00), _leg("call", 1.20)], None) == ""


def test_classify_risk_reversal_carries_delta_bucket():
    # RR = buy call + sell put (opposite sides). Like the strangle it now carries
    # its Δ bucket ("risk reversal 10d") so the blotter shows the wing the trader
    # picked instead of re-bucketing the live strike to the wrong pillar.
    from core.trade_preview import Leg, classify_legs

    def _leg(ct: str, k: float, side: str) -> Leg:
        return Leg(leg_idx=0, contract_type=ct, tenor="1M", expiry="", dte=30,
                   strike=k, qty_factor=1, side=side, entry_iv_pct=7.0)
    legs = [_leg("call", 1.20, "BUY"), _leg("put", 1.00, "SELL")]
    assert classify_legs(legs, 1.10) == "risk reversal 10d"  # wide OTM wings → 10Δ
    assert classify_legs(legs, None) == "risk reversal"       # no spot → bare


def test_classify_legs_calendar_two_tenors():
    # a calendar = one type, two expiries — regardless of side. Both the
    # (theoretical) same-side shape and the REAL opposite-side shape the order
    # builder sends (sell near / buy far) must classify as a calendar, not
    # fall through to "custom".
    same_side = build_from_legs(
        [
            {"contract_type": "call", "side": "BUY", "tenor": "1M", "delta_pillar": "atm"},
            {"contract_type": "call", "side": "BUY", "tenor": "3M", "delta_pillar": "atm"},
        ],
        _mock_surface(),
    )
    assert classify_legs(same_side.legs) == "calendar"
    real = build_from_legs(
        [
            {"contract_type": "call", "side": "SELL", "tenor": "1M", "delta_pillar": "atm"},
            {"contract_type": "call", "side": "BUY", "tenor": "3M", "delta_pillar": "atm"},
        ],
        _mock_surface(),
    )
    assert classify_legs(real.legs) == "calendar"


def test_the_browser_sketch_uses_the_server_commission_table():
    """One table (#483): the ticket showed 2.1 per contract from a browser
    constant next to the server's 2.0 / 2.40. The sketch shown before a
    preview reads a copy of the server's table; this keeps the copy equal."""
    import re
    from pathlib import Path

    from core.trade_preview import COMMISSION_USD

    ts = (Path(__file__).resolve().parents[3]
          / "frontend/src/voldesk/components/ticketNumbers.ts").read_text(encoding="utf-8")
    m = re.search(r"COMMISSION_USD = \{([^}]*)\}", ts)
    assert m, "COMMISSION_USD not found in ticketNumbers.ts"
    browser = {k: float(v) for k, v in re.findall(r"(\w+):\s*([0-9.]+)", m.group(1))}
    assert browser == COMMISSION_USD
