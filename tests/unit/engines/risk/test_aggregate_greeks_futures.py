"""Regression: futures positions must contribute delta in _aggregate_greeks.

Positions carry the canonical ``instrument_type == "FUTURE"``
(shared.contracts), not the IB secType ``"FUT"``. The engine used to
compare against ``"FUT"``, silently zeroing every futures delta —
including delta hedges — in the published portfolio greeks.
"""
from __future__ import annotations

import pytest

from engines.risk.engine import RiskEngine
from shared.contracts import INSTRUMENT_FUTURE

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
    totals = _engine()._aggregate_greeks(
        [{"instrument_type": INSTRUMENT_FUTURE, "quantity": 3}],
        F=1.10,
        surface={},
    )
    assert totals["delta"] == pytest.approx(3.0)
    assert totals["gamma"] == 0.0 and totals["vega"] == 0.0


def test_short_future_contributes_negative_delta():
    totals = _engine()._aggregate_greeks(
        [{"instrument_type": INSTRUMENT_FUTURE, "quantity": -2}],
        F=1.10,
        surface={},
    )
    assert totals["delta"] == pytest.approx(-2.0)


def test_ib_sectype_literal_is_not_a_position_instrument_type():
    # The IB secType spelling must NOT be honoured here — positions are
    # normalized to the canonical constants before reaching the engine.
    totals = _engine()._aggregate_greeks(
        [{"instrument_type": "FUT", "quantity": 5}],
        F=1.10,
        surface={},
    )
    assert totals["delta"] == 0.0
