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


def test_every_cap_is_checked_on_the_post_trade_book() -> None:
    big = TradeGreeks(delta_usd=200_000.0, vega_usd_per_volpt=4_500.0, gamma_usd_per_pip=2_600.0, notional_eur=6_000_000.0)
    r = evaluate_pretrade(replace(OK, trade=big))
    assert set(r.failures) == {"cap_delta", "cap_vega", "cap_gamma", "cap_notional"}


def test_caps_that_could_not_be_established_refuse() -> None:
    zero = Caps(delta_usd=0.0, vega_usd_per_volpt=0.0, gamma_usd_per_pip=0.0, max_notional_eur=5e6)
    assert "caps_not_established" in evaluate_pretrade(replace(OK, caps=zero)).failures
    assert "caps_unavailable" in evaluate_pretrade(replace(OK, caps=None)).failures
    assert "book_unavailable" in evaluate_pretrade(replace(OK, book=None)).failures
    assert "trade_greeks_unavailable" in evaluate_pretrade(replace(OK, trade=None)).failures


def test_unknown_is_not_a_pass() -> None:
    """03#9 / 04#11: no heartbeat, no account type, no surface age -> refuse."""
    assert "risk_engine_stale" in evaluate_pretrade(replace(OK, risk_heartbeat_age_s=None)).failures
    assert "risk_engine_stale" in evaluate_pretrade(replace(OK, risk_heartbeat_age_s=45.0)).failures
    assert "account_type_mismatch" in evaluate_pretrade(replace(OK, ib_account_type=None)).failures
    assert "account_type_mismatch" in evaluate_pretrade(replace(OK, ib_account_type="live")).failures
    assert "surface_stale" in evaluate_pretrade(replace(OK, surface_age_s=None)).failures
    assert "surface_stale" in evaluate_pretrade(replace(OK, surface_age_s=600.0)).failures


def test_mock_mode_skips_the_live_session_checks_but_keeps_caps_and_halt() -> None:
    mock = replace(OK, live_mode=False, risk_heartbeat_age_s=None, ib_account_type=None, surface_age_s=None)
    assert evaluate_pretrade(mock).passed
    assert "trading_halted" in evaluate_pretrade(replace(mock, halted=True)).failures
    assert "cap_vega" in evaluate_pretrade(replace(mock, trade=replace(OK.trade, vega_usd_per_volpt=9_000.0))).failures


def test_allow_list_partial_pricing_and_arbitrage_flag() -> None:
    assert "symbol_not_allowed" in evaluate_pretrade(replace(OK, symbols=("GBP",))).failures
    assert "symbol_not_allowed" in evaluate_pretrade(replace(OK, symbols=())).failures
    assert "book_partially_priced" in evaluate_pretrade(replace(OK, book=replace(OK.book, n_priced=3))).failures
    assert "surface_arbitrage" in evaluate_pretrade(replace(OK, arb_ok=False)).failures
    assert evaluate_pretrade(replace(OK, arb_ok=None)).passed   # not reported != violated


def test_all_failures_are_reported_at_once() -> None:
    r = evaluate_pretrade(replace(OK, halted=True, ib_account_type="live", symbols=("GBP",)))
    assert {"trading_halted", "account_type_mismatch", "symbol_not_allowed"} <= set(r.failures)
