"""Spot FX is a known, priced instrument (#460) and an unpriced line is named (#459).

Before: IB's CASH position ``EUR.USD`` matched no contract pattern. It was
labelled "Future - 6E", its entry price was divided by 125 000, the risk
engine skipped it (no delta, no mark, no P&L), and the published aggregate
still claimed to cover the whole book.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from core.contracts import (
    INSTRUMENT_FUTURE,
    INSTRUMENT_OPTION,
    INSTRUMENT_SPOT,
    is_linear,
    parse_local_symbol,
)
from core.products import product_label_from_symbol
from core.risk.greek_scaling import position_greeks
from core.risk.pretrade import BookGreeks, Caps, GateInputs, TradeGreeks, evaluate_pretrade
from engines.risk.engine import RiskEngine

pytestmark = pytest.mark.unit


def test_spot_local_symbol_parses_with_multiplier_one() -> None:
    spec = parse_local_symbol("EUR.USD")
    assert spec is not None
    assert spec.instrument_type == INSTRUMENT_SPOT
    assert spec.symbol == "EUR" and spec.multiplier == 1.0
    assert spec.strike is None and spec.option_type is None


def test_listed_contracts_still_parse_as_before() -> None:
    assert parse_local_symbol("6EH7").instrument_type == INSTRUMENT_FUTURE
    assert parse_local_symbol("EUUG7 C1110").instrument_type == INSTRUMENT_OPTION
    assert parse_local_symbol("GBP.USD") is None   # only the pair the desk trades
    assert parse_local_symbol("???") is None


def test_linear_means_future_or_spot() -> None:
    assert is_linear(INSTRUMENT_FUTURE) and is_linear(INSTRUMENT_SPOT)
    assert not is_linear(INSTRUMENT_OPTION) and not is_linear(None)


def test_spot_label_is_not_a_future_label() -> None:
    assert product_label_from_symbol("EUR.USD", None) == "Spot EUR/USD"
    assert product_label_from_symbol("6EH7", None) == "Future - 6E"
    assert product_label_from_symbol("EUUG7 P1120", None) == "Vanilla Put"


def test_spot_greeks_are_the_signed_quantity() -> None:
    g = position_greeks(
        instrument_type=INSTRUMENT_SPOT, right=None, qty=-22_533.0, mult=1.0, F=1.11,
        K=None, T=None, iv=None,
    )
    assert g is not None
    assert g.delta_eur == pytest.approx(-22_533.0)
    assert g.delta_usd == pytest.approx(-22_533.0 * 1.11)
    assert g.gamma_usd_per_pip == 0.0 and g.vega_usd_per_volpt == 0.0


def _engine() -> RiskEngine:
    return RiskEngine(ib=object(), redis=object(), symbol="EURUSD", ib_host="localhost", ib_port=4002, client_id=99)


def test_aggregate_counts_the_spot_position() -> None:
    totals = _engine()._aggregate_greeks(
        [
            {"instrument_type": INSTRUMENT_FUTURE, "quantity": 7, "symbol": "EUR", "multiplier": 125_000.0},
            {"instrument_type": INSTRUMENT_SPOT, "quantity": -22_533, "symbol": "EUR", "multiplier": 1.0},
        ],
        F=1.10, surface={},
    )
    assert totals["delta_eur"] == pytest.approx(7 * 125_000.0 - 22_533.0)
    assert totals["n_positions"] == 2 and totals["n_priced"] == 2


_OK = GateInputs(
    halted=False, halt_reason=None, live_mode=True,
    risk_heartbeat_age_s=1.0, max_risk_heartbeat_age_s=30.0,
    ib_account_type="paper", required_account_type="paper",
    book=BookGreeks(delta_usd=0.0, vega_usd_per_volpt=0.0, gamma_usd_per_pip=0.0, n_positions=2, n_priced=2),
    trade=TradeGreeks(delta_usd=1.0, vega_usd_per_volpt=1.0, gamma_usd_per_pip=1.0, notional_eur=1.0),
    caps=Caps(delta_usd=100.0, vega_usd_per_volpt=100.0, gamma_usd_per_pip=100.0, max_notional_eur=100.0),
    symbols=("EUR",), allowed_symbols=("EUR",),
    surface_age_s=1.0, max_surface_age_s=120.0, arb_ok=True,
)


def test_gate_names_the_lines_it_could_not_price() -> None:
    assert evaluate_pretrade(_OK).passed
    res = evaluate_pretrade(replace(_OK, book=replace(_OK.book, n_priced=1, unpriced=("XYZ.ABC",))))
    assert not res.passed and "book_partially_priced" in res.failures
    assert res.details["book_unpriced"] == 1
    assert res.details["book_unpriced_lines"] == ["XYZ.ABC"]
