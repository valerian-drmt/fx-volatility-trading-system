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


def test_the_application_computes_no_limit_and_asks_for_no_confirmation() -> None:
    """Owner's rule (2026-11-13): the application is not the trader. Whatever
    the size of the trade against the account, nothing is refused, nothing is
    flagged and nothing is to confirm."""
    big = TradeGreeks(delta_usd=200_000.0, vega_usd_per_volpt=4_500.0, gamma_usd_per_pip=2_600.0, notional_eur=6_000_000.0)
    r = evaluate_pretrade(replace(OK, trade=big))
    assert r.passed and r.warnings == [] and r.failures == []
    assert r.details["caps"] == {"delta_usd": None, "vega_usd_per_volpt": None, "gamma_usd_per_pip": None,
                                 "max_notional_eur": None}


def test_a_limit_the_trader_set_refuses() -> None:
    big = TradeGreeks(delta_usd=200_000.0, vega_usd_per_volpt=4_500.0, gamma_usd_per_pip=2_600.0, notional_eur=6_000_000.0)
    hard = {"delta": 100_000.0, "vega": 2_000.0, "gamma": 1_000.0, "notional": 5_000_000.0}
    r = evaluate_pretrade(replace(OK, trade=big, hard_limits=hard, confirmed=True))
    assert set(r.failures) == {"cap_delta", "cap_vega", "cap_gamma", "cap_notional"}
    one = evaluate_pretrade(replace(OK, trade=big, hard_limits={"vega": 1_000.0}))
    assert one.failures == ["cap_vega"] and one.warnings == []
    assert one.details["caps"]["vega_usd_per_volpt"] == 1_000.0 and one.details["caps"]["delta_usd"] is None


def test_derived_levels_are_not_read_but_unusable_data_refuses() -> None:
    tiny = Caps(delta_usd=1.0, vega_usd_per_volpt=1.0, gamma_usd_per_pip=1.0, max_notional_eur=1.0)
    for caps in (tiny, None):
        r = evaluate_pretrade(replace(OK, caps=caps))
        assert r.passed and r.warnings == []
    assert "book_unavailable" in evaluate_pretrade(replace(OK, book=None)).failures
    assert "trade_greeks_unavailable" in evaluate_pretrade(replace(OK, trade=None)).failures


def test_a_premium_the_account_cannot_pay_is_a_fact() -> None:
    """A concrete refusal: the premium is larger than the whole account.
    Margin is the broker's decision: nothing is said about it here."""
    r = evaluate_pretrade(replace(OK, premium_to_pay_usd=250_000.0, net_liquidation_usd=170_000.0,
                                  available_funds_usd=90_000.0))
    assert r.failures == ["insufficient_funds"] and r.details["net_liquidation_usd"] == 170_000.0
    tight = evaluate_pretrade(replace(OK, premium_to_pay_usd=120_000.0, net_liquidation_usd=170_000.0,
                                      available_funds_usd=90_000.0))
    assert tight.passed and tight.warnings == []
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
    assert evaluate_pretrade(over).passed
    assert "cap_vega" in evaluate_pretrade(replace(over, hard_limits={"vega": 5_000.0})).failures


def test_allow_list_partial_pricing_and_arbitrage_flag() -> None:
    assert "symbol_not_allowed" in evaluate_pretrade(replace(OK, symbols=("GBP",))).failures
    assert "symbol_not_allowed" in evaluate_pretrade(replace(OK, symbols=())).failures
    partial = evaluate_pretrade(replace(OK, book=replace(OK.book, n_priced=3)))
    assert partial.passed and partial.warnings == [] and "book_unpriced" in partial.details
    assert "surface_arbitrage" in evaluate_pretrade(replace(OK, arb_ok=False)).failures
    assert evaluate_pretrade(replace(OK, arb_ok=None)).passed   # not reported != violated


def test_all_failures_are_reported_at_once() -> None:
    r = evaluate_pretrade(replace(OK, halted=True, ib_account_type="live", symbols=("GBP",)))
    assert {"trading_halted", "account_type_mismatch", "symbol_not_allowed"} <= set(r.failures)
