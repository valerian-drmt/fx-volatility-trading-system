"""Unit tests for the IB ↔ trade_positions reconciliation matcher.

Covers only the pure key-builder ``_structure_order_to_ib_key`` — full
DB+IB integration of ``reconcile_trade_positions`` lives in
tests/integration/pipeline_position_sync (gated by IB_RUN_INTEGRATION).

After migration 025, the canonical key is the IB ``localSymbol`` (string)
instead of a 5-tuple of contract attributes.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from engines.execution.position_sync import _structure_order_to_ib_key


def _leg(**kw) -> SimpleNamespace:
    base = {
        "contract_symbol": "EUR",
        "contract_type": "call",
        "contract_strike": 1.10,
        "contract_expiry": date(2026, 6, 19),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_call_leg_keys_to_option_localsymbol():
    # Call @1.10 expiry Jun 2026 → "EUUM6 C1100"
    assert _structure_order_to_ib_key(_leg(contract_type="call")) == "EUUM6 C1100"


def test_put_leg_keys_to_option_localsymbol():
    assert _structure_order_to_ib_key(_leg(contract_type="put")) == "EUUM6 P1100"


def test_future_leg_keys_to_localsymbol():
    # EUR future expiry Jun 2026 → "6EM6"
    key = _structure_order_to_ib_key(
        _leg(contract_type="future", contract_strike=None)
    )
    assert key == "6EM6"


def test_m6e_future_leg_keys_to_m6e_localsymbol():
    key = _structure_order_to_ib_key(
        _leg(contract_type="future", contract_strike=None, contract_symbol="M6E")
    )
    assert key == "M6EM6"


def test_unknown_contract_type_returns_none():
    assert _structure_order_to_ib_key(_leg(contract_type="weird")) is None


def test_missing_expiry_returns_none():
    assert _structure_order_to_ib_key(_leg(contract_expiry=None)) is None
