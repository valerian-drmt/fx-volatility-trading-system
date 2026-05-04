"""Unit tests for core.execution.contract_builder."""
from __future__ import annotations

from datetime import date

import pytest

from core.execution.contract_builder import (
    build_contract_kwargs,
    build_order_kwargs,
    can_use_combo,
)


def test_build_contract_call_eur():
    out = build_contract_kwargs(
        contract_type="call", expiry=date(2026, 6, 19), strike=1.0850,
    )
    assert out["symbol"] == "EUR"
    assert out["secType"] == "FOP"
    assert out["lastTradeDateOrContractMonth"] == "20260619"
    assert out["right"] == "C"
    assert out["strike"] == pytest.approx(1.0850)
    assert out["tradingClass"] == "EUU"


def test_build_contract_put_iso_string_expiry():
    out = build_contract_kwargs(
        contract_type="put", expiry="2026-09-18", strike=1.10,
    )
    assert out["right"] == "P"
    assert out["lastTradeDateOrContractMonth"] == "20260918"


def test_build_contract_rejects_bad_type():
    with pytest.raises(ValueError):
        build_contract_kwargs(
            contract_type="straddle", expiry=date(2026, 6, 19), strike=1.0,
        )


def test_build_order_buy():
    out = build_order_kwargs(side="buy", qty=5, limit_price=1.234)
    assert out == {"action": "BUY", "totalQuantity": 5, "lmtPrice": 1.234, "tif": "DAY"}


def test_build_order_validation():
    with pytest.raises(ValueError):
        build_order_kwargs(side="HOLD", qty=1, limit_price=1.0)
    with pytest.raises(ValueError):
        build_order_kwargs(side="BUY", qty=0, limit_price=1.0)
    with pytest.raises(ValueError):
        build_order_kwargs(side="BUY", qty=1, limit_price=0.0)


def test_combo_yes_when_legs_share_expiry():
    legs = [
        {"expiry": date(2026, 6, 19), "contract_symbol": "EUR",
         "contract_exchange": "CME", "contract_currency": "USD"},
        {"expiry": date(2026, 6, 19), "contract_symbol": "EUR",
         "contract_exchange": "CME", "contract_currency": "USD"},
    ]
    assert can_use_combo(legs) is True


def test_combo_no_when_calendar():
    legs = [
        {"expiry": date(2026, 6, 19), "contract_symbol": "EUR",
         "contract_exchange": "CME", "contract_currency": "USD"},
        {"expiry": date(2026, 9, 18), "contract_symbol": "EUR",
         "contract_exchange": "CME", "contract_currency": "USD"},
    ]
    assert can_use_combo(legs) is False


def test_combo_no_for_single_leg():
    assert can_use_combo([{"expiry": date(2026, 6, 19), "contract_symbol": "EUR",
                           "contract_exchange": "CME", "contract_currency": "USD"}]) is False


def test_combo_no_for_empty():
    assert can_use_combo([]) is False
