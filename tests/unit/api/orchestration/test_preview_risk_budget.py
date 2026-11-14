"""The preview carries the gate's verdict (#479).

Prod, 2026-11-13: BUY 40 puts 3M. The ticket (browser model) showed delta
-$5.8k and vega +$312; the gate refused on delta $2.58M against $90.6k, vega
$10.9k against $1.9k, gamma $5.9k against $1.0k. The ticket now gets the
gate's own numbers with the preview.
"""
from __future__ import annotations

import pytest

from api.orchestration.pretrade_gate import budget_from_gate
from core.risk.pretrade import BookGreeks, Caps, GateInputs, TradeGreeks, evaluate_pretrade

pytestmark = pytest.mark.unit


def _inputs(**over):
    base = dict(
        halted=False, halt_reason=None, live_mode=False,
        risk_heartbeat_age_s=None, max_risk_heartbeat_age_s=30.0,
        ib_account_type=None, required_account_type="paper",
        book=BookGreeks(delta_usd=0.0, vega_usd_per_volpt=0.0, gamma_usd_per_pip=0.0, n_positions=0, n_priced=0),
        trade=TradeGreeks(delta_usd=-2_575_628.94, vega_usd_per_volpt=10_927.32,
                          gamma_usd_per_pip=5_875.6, notional_eur=5_000_000.0),
        caps=Caps(delta_usd=90_645.12, vega_usd_per_volpt=1_888.44, gamma_usd_per_pip=1_018.33,
                  max_notional_eur=5_000_000.0),
        symbols=("EUR",), allowed_symbols=("EUR", "M6E"),
        surface_age_s=None, max_surface_age_s=270.0, arb_ok=None,
    )
    base.update(over)
    return GateInputs(**base)


def test_budget_rows_are_the_gate_numbers_and_carry_no_level_of_the_applications_own() -> None:
    budget = budget_from_gate(evaluate_pretrade(_inputs()), qty=40)
    rows = {r["key"]: r for r in budget["rows"]}
    assert budget["failures"] == [] and budget["max_qty"] is None
    assert rows["delta"]["after"] == -2_575_628.94 and rows["vega"]["trade"] == 10_927.32
    assert all(r["cap"] is None and r["used"] is None and not r["breached"] for r in budget["rows"])
    assert rows["notional"]["before"] is None, "notional is per order, there is no book notional"
    # the same trade against a limit the trader set: refused, with his number
    hard = budget_from_gate(evaluate_pretrade(_inputs(hard_limits={"vega": 5_000.0})), qty=40)
    hrows = {r["key"]: r for r in hard["rows"]}
    assert hard["failures"] == ["cap_vega"] and hrows["vega"]["breached"] is True and hrows["vega"]["cap"] == 5_000.0
    assert hrows["vega"]["used"] == pytest.approx(2.185, abs=0.01) and hrows["delta"]["cap"] is None
    assert hard["max_qty"] == 18


def test_a_trade_inside_the_traders_limit_shows_what_it_uses() -> None:
    small = TradeGreeks(delta_usd=-20_000.0, vega_usd_per_volpt=270.0, gamma_usd_per_pip=150.0, notional_eur=125_000.0)
    book = BookGreeks(delta_usd=10_000.0, vega_usd_per_volpt=500.0, gamma_usd_per_pip=100.0, n_positions=2, n_priced=2)
    budget = budget_from_gate(evaluate_pretrade(_inputs(trade=small, book=book, hard_limits={"vega": 1_888.44})))
    rows = {r["key"]: r for r in budget["rows"]}
    assert budget["failures"] == []
    assert (rows["vega"]["before"], rows["vega"]["trade"], rows["vega"]["after"]) == (500.0, 270.0, 770.0)
    assert rows["vega"]["used"] == pytest.approx(770.0 / 1_888.44, abs=1e-4)
    assert rows["delta"]["after"] == -10_000.0


def test_session_only_failures_are_not_announced_on_a_preview() -> None:
    live = _inputs(live_mode=True, risk_heartbeat_age_s=None, ib_account_type=None, surface_age_s=None,
                   hard_limits={"vega": 5_000.0})
    result = evaluate_pretrade(live)
    assert {"account_type_mismatch", "risk_engine_stale", "surface_stale"} <= set(result.failures)
    assert budget_from_gate(result)["failures"] == ["cap_vega"]


def test_without_derived_levels_the_rows_are_still_shown() -> None:
    budget = budget_from_gate(evaluate_pretrade(_inputs(caps=None)))
    assert budget["failures"] == [] and len(budget["rows"]) == 4
