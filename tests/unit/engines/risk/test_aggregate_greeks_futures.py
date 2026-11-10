"""Regression: futures positions must contribute delta in _aggregate_greeks.

Positions carry the canonical ``instrument_type == "FUTURE"``
(core.contracts), not the IB secType ``"FUT"``. The engine used to
compare against ``"FUT"``, silently zeroing every futures delta —
including delta hedges — in the published portfolio greeks.
"""
from __future__ import annotations

import pytest

from core.contracts import INSTRUMENT_FUTURE
from engines.risk.engine import RiskEngine

pytestmark = pytest.mark.unit


def _engine() -> RiskEngine:
    # _aggregate_greeks is pure — no IB/Redis calls — so dummies suffice.
    return RiskEngine(
        ib=object(),
        redis=object(),
        symbol="EURUSD",
        ib_host="localhost",
        ib_port=4002,
        client_id=99,
    )


def test_future_position_contributes_full_delta():
    # Units since #313: delta in USD (= qty × 125 000 EUR × spot), delta_eur alongside.
    totals = _engine()._aggregate_greeks(
        [{"instrument_type": INSTRUMENT_FUTURE, "quantity": 3, "symbol": "EUR"}],
        F=1.10,
        surface={},
    )
    assert totals["delta_eur"] == pytest.approx(375_000.0)
    assert totals["delta"] == pytest.approx(375_000.0 * 1.10)
    assert totals["gamma"] == 0.0 and totals["vega"] == 0.0
    assert totals["n_positions"] == 1 and totals["n_priced"] == 1


def test_short_future_contributes_negative_delta():
    totals = _engine()._aggregate_greeks(
        [{"instrument_type": INSTRUMENT_FUTURE, "quantity": -2, "symbol": "EUR"}],
        F=1.10,
        surface={},
    )
    assert totals["delta_eur"] == pytest.approx(-250_000.0)


def test_micro_future_counts_a_tenth_of_a_full_one():
    # R3-07: an M6E (12 500) used to count exactly like a 6E (125 000).
    full = _engine()._aggregate_greeks([{"instrument_type": INSTRUMENT_FUTURE, "quantity": 1, "symbol": "EUR"}], F=1.10, surface={})
    micro = _engine()._aggregate_greeks([{"instrument_type": INSTRUMENT_FUTURE, "quantity": 1, "symbol": "M6E"}], F=1.10, surface={})
    assert micro["delta_eur"] == pytest.approx(full["delta_eur"] / 10)


def test_published_option_greeks_carry_the_multiplier():
    """R3-07: the WS payload was 125 000x below the table for the same lots."""
    from core.risk.greek_scaling import option_greeks
    surface = {"1M": {"atm": {"iv": 0.08, "strike": 1.10}}}
    pos = {"instrument_type": "OPTION", "option_type": "C", "quantity": 10, "strike": 1.10,
           "T": 30 / 365, "tenor": "1M", "symbol": "EUR", "multiplier": 125_000}
    totals = _engine()._aggregate_greeks([pos], F=1.10, surface=surface)
    g = option_greeks(qty=10, mult=125_000, F=1.10, K=1.10, T=30 / 365, iv=0.08, right="C")
    assert totals["delta"] == pytest.approx(g.delta_usd)
    assert totals["vega"] == pytest.approx(g.vega_usd_per_volpt)
    assert totals["gamma"] == pytest.approx(g.gamma_usd_per_pip)
    assert 1_000 < totals["vega"] < 2_000


def test_ib_sectype_literal_is_not_a_position_instrument_type():
    # The IB secType spelling must NOT be honoured here — positions are
    # normalized to the canonical constants before reaching the engine.
    totals = _engine()._aggregate_greeks(
        [{"instrument_type": "FUT", "quantity": 5}],
        F=1.10,
        surface={},
    )
    assert totals["delta"] == 0.0
