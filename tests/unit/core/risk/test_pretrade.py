"""core.risk.pretrade — the fail-closed gate (#315)."""
from __future__ import annotations

from dataclasses import replace

from core.risk.pretrade import BookGreeks, Caps, GateInputs, TradeGreeks, evaluate_pretrade

OK = GateInputs(
    halted=False, halt_reason=None, live_mode=True,
    risk_heartbeat_age_s=3.0, max_risk_heartbeat_age_s=30.0,
    ib_account_type="paper", required_account_type="paper",
    book=BookGreeks(delta_usd=100_000.0, vega_usd_per_volpt=1_000.0, gamma_usd_per_pip=500.0, n_positions=4, n_priced=4),
    trade=TradeGreeks(delta_usd=20_000.0, vega_usd_per_volpt=300.0, gamma_usd_per_pip=100.0, notional_eur=1_250_000.0),
    caps=Caps(delta_usd=243_600.0, vega_usd_per_volpt=5_075.0, gamma_usd_per_pip=3_007.0, max_notional_eur=5_000_000.0),
    symbols=("EUR",), allowed_symbols=("EUR", "M6E"),
    surface_age_s=20.0, max_surface_age_s=120.0, arb_ok=True,
)


def test_a_healthy_book_passes_with_the_post_trade_numbers_reported() -> None:
    r = evaluate_pretrade(OK)
    assert r.passed and r.failures == []
    assert r.details["post_trade"]["vega_usd_per_volpt"] == 1_300.0


def test_kill_switch_blocks_and_an_unreadable_flag_blocks_too() -> None:
    assert "trading_halted" in evaluate_pretrade(replace(OK, halted=True, halt_reason="ops")).failures
    assert "halt_flag_unreadable" in evaluate_pretrade(replace(OK, halted=None)).failures


def test_a_level_the_code_derives_asks_for_a_confirmation_and_never_refuses() -> None:
    """Owner's rule (2026-11-13): the trader decides the limits. A trade over
    a level computed from the account's size is the trader's call."""
    big = TradeGreeks(delta_usd=200_000.0, vega_usd_per_volpt=4_500.0, gamma_usd_per_pip=2_600.0, notional_eur=6_000_000.0)
    r = evaluate_pretrade(replace(OK, trade=big))
    assert r.failures == ["limits_need_confirmation"]
    assert set(r.warnings) == {"over_delta", "over_vega", "over_gamma", "over_notional"}
    confirmed = evaluate_pretrade(replace(OK, trade=big, confirmed=True))
    assert confirmed.passed and set(confirmed.warnings) == set(r.warnings)


def test_a_limit_the_trader_set_refuses() -> None:
    big = TradeGreeks(delta_usd=200_000.0, vega_usd_per_volpt=4_500.0, gamma_usd_per_pip=2_600.0, notional_eur=6_000_000.0)
    caps = OK.caps
    hard = {"delta": caps.delta_usd, "vega": caps.vega_usd_per_volpt, "gamma": caps.gamma_usd_per_pip,
            "notional": caps.max_notional_eur}
    r = evaluate_pretrade(replace(OK, trade=big, hard_limits=hard, confirmed=True))
    assert set(r.failures) == {"cap_delta", "cap_vega", "cap_gamma", "cap_notional"}, "a confirmation does not lift it"
    one = evaluate_pretrade(replace(OK, trade=big, hard_limits={"vega": 1_000.0}, confirmed=True))
    assert one.failures == ["cap_vega"] and "over_delta" in one.warnings


def test_missing_reference_levels_are_said_not_refused() -> None:
    zero = Caps(delta_usd=0.0, vega_usd_per_volpt=0.0, gamma_usd_per_pip=0.0, max_notional_eur=5e6)
    for caps in (zero, None):
        r = evaluate_pretrade(replace(OK, caps=caps, confirmed=True))
        assert r.passed and "reference_limits_unavailable" in r.warnings
    assert "book_unavailable" in evaluate_pretrade(replace(OK, book=None)).failures
    assert "trade_greeks_unavailable" in evaluate_pretrade(replace(OK, trade=None)).failures


def test_a_premium_the_account_cannot_pay_is_a_fact() -> None:
    """A concrete refusal: the premium is larger than the whole account."""
    r = evaluate_pretrade(replace(OK, premium_to_pay_usd=250_000.0, net_liquidation_usd=170_000.0,
                                  available_funds_usd=90_000.0, confirmed=True))
    assert r.failures == ["insufficient_funds"] and r.details["net_liquidation_usd"] == 170_000.0
    tight = evaluate_pretrade(replace(OK, premium_to_pay_usd=120_000.0, net_liquidation_usd=170_000.0,
                                      available_funds_usd=90_000.0))
    assert tight.failures == ["limits_need_confirmation"] and tight.warnings == ["over_available_funds"]
    assert evaluate_pretrade(replace(OK, premium_to_pay_usd=5_000.0, net_liquidation_usd=170_000.0,
                                     available_funds_usd=90_000.0)).passed
    assert evaluate_pretrade(replace(OK, premium_to_pay_usd=9e9)).passed, "no account snapshot: nothing is concluded"


def test_unknown_is_not_a_pass() -> None:
    """03#9 / 04#11: no heartbeat, no account type, no surface age -> refuse."""
    assert "risk_engine_stale" in evaluate_pretrade(replace(OK, risk_heartbeat_age_s=None)).failures
    assert "risk_engine_stale" in evaluate_pretrade(replace(OK, risk_heartbeat_age_s=45.0)).failures
    assert "account_type_mismatch" in evaluate_pretrade(replace(OK, ib_account_type=None)).failures
    assert "account_type_mismatch" in evaluate_pretrade(replace(OK, ib_account_type="live")).failures
    assert "surface_stale" in evaluate_pretrade(replace(OK, surface_age_s=None)).failures
    assert "surface_stale" in evaluate_pretrade(replace(OK, surface_age_s=600.0)).failures


def test_mock_mode_skips_the_live_session_checks_but_keeps_limits_and_halt() -> None:
    mock = replace(OK, live_mode=False, risk_heartbeat_age_s=None, ib_account_type=None, surface_age_s=None)
    assert evaluate_pretrade(mock).passed
    assert "trading_halted" in evaluate_pretrade(replace(mock, halted=True)).failures
    over = replace(mock, trade=replace(OK.trade, vega_usd_per_volpt=9_000.0))
    assert evaluate_pretrade(over).failures == ["limits_need_confirmation"]
    assert "cap_vega" in evaluate_pretrade(replace(over, hard_limits={"vega": 5_000.0})).failures


def test_allow_list_partial_pricing_and_arbitrage_flag() -> None:
    assert "symbol_not_allowed" in evaluate_pretrade(replace(OK, symbols=("GBP",))).failures
    assert "symbol_not_allowed" in evaluate_pretrade(replace(OK, symbols=())).failures
    partial = evaluate_pretrade(replace(OK, book=replace(OK.book, n_priced=3), confirmed=True))
    assert partial.passed and "book_partially_priced" in partial.warnings
    assert "surface_arbitrage" in evaluate_pretrade(replace(OK, arb_ok=False)).failures
    assert evaluate_pretrade(replace(OK, arb_ok=None)).passed   # not reported != violated


def test_all_failures_are_reported_at_once() -> None:
    r = evaluate_pretrade(replace(OK, halted=True, ib_account_type="live", symbols=("GBP",)))
    assert {"trading_halted", "account_type_mismatch", "symbol_not_allowed"} <= set(r.failures)
