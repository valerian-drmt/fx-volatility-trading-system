"""Unit tests for core.execution.contract_builder."""
from __future__ import annotations

from datetime import date

import pytest

from core.execution.contract_builder import (
    build_combo,
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
    # FOP expiry now reduced to YYYYMM so IB resolves to the monthly
    # listing (3rd Friday) without depending on the caller knowing the
    # exact listed date.
    assert out["lastTradeDateOrContractMonth"] == "202606"
    assert out["right"] == "C"
    assert out["strike"] == pytest.approx(1.0850)
    assert out["tradingClass"] == "EUU"


def test_build_contract_put_iso_string_expiry():
    out = build_contract_kwargs(
        contract_type="put", expiry="2026-09-18", strike=1.10,
    )
    assert out["right"] == "P"
    assert out["lastTradeDateOrContractMonth"] == "202609"


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


# --------------------------------------------------------------------------
# build_combo — BAG assembly
# --------------------------------------------------------------------------

def test_build_combo_risk_reversal_1_1_net_zero():
    """RR 25× : BUY call @0.012, SELL put @0.012 → ratio 1:1, net premium ≈ 0."""
    out = build_combo(
        symbol="EUR", exchange="CME", currency="USD",
        legs=[
            {"conId": 111, "side": "BUY", "qty": 25, "limit_price": 0.012},
            {"conId": 222, "side": "SELL", "qty": 25, "limit_price": 0.012},
        ],
    )
    assert out["base_qty"] == 25
    assert out["order"] == {"action": "BUY", "totalQuantity": 25, "lmtPrice": 0.0}
    legs = out["contract"]["comboLegs"]
    assert [lg["ratio"] for lg in legs] == [1, 1]
    assert [lg["conId"] for lg in legs] == [111, 222]
    assert [lg["action"] for lg in legs] == ["BUY", "SELL"]
    assert out["contract"]["secType"] == "BAG"
    assert all(lg["exchange"] == "CME" for lg in legs)


def test_build_combo_butterfly_1_2_1():
    """Fly 25/50/25 → GCD 25 → ratios 1:2:1, base_qty 25."""
    out = build_combo(
        symbol="EUR", exchange="CME", currency="USD",
        legs=[
            {"conId": 1, "side": "BUY", "qty": 25, "limit_price": 0.010},
            {"conId": 2, "side": "SELL", "qty": 50, "limit_price": 0.006},
            {"conId": 3, "side": "BUY", "qty": 25, "limit_price": 0.003},
        ],
    )
    assert out["base_qty"] == 25
    assert [lg["ratio"] for lg in out["contract"]["comboLegs"]] == [1, 2, 1]
    # net = +1*0.010 - 2*0.006 + 1*0.003 = 0.001 (small debit)
    assert out["order"]["lmtPrice"] == pytest.approx(0.001)


def test_build_combo_market_when_no_leg_prices():
    """Legs with no limit_price (desk sends MKT) → no lmtPrice → market BAG."""
    out = build_combo(
        symbol="EUR", exchange="CME", currency="USD",
        legs=[
            {"conId": 1, "side": "BUY", "qty": 25, "limit_price": None},
            {"conId": 2, "side": "BUY", "qty": 25, "limit_price": None},
        ],
    )
    assert "lmtPrice" not in out["order"]
    assert out["order"] == {"action": "BUY", "totalQuantity": 25}
    assert [lg["ratio"] for lg in out["contract"]["comboLegs"]] == [1, 1]


def test_build_combo_partial_prices_still_market():
    """Mixed (some priced, some not) → treated as market (no partial net)."""
    out = build_combo(
        symbol="EUR", exchange="CME", currency="USD",
        legs=[
            {"conId": 1, "side": "BUY", "qty": 10, "limit_price": 0.01},
            {"conId": 2, "side": "SELL", "qty": 10, "limit_price": None},
        ],
    )
    assert "lmtPrice" not in out["order"]


def test_build_combo_net_credit_is_negative():
    """SELL-rich structure → signed net < 0 (credit), rides as negative lmtPrice."""
    out = build_combo(
        symbol="EUR", exchange="CME", currency="USD",
        legs=[
            {"conId": 1, "side": "BUY", "qty": 10, "limit_price": 0.004},
            {"conId": 2, "side": "SELL", "qty": 10, "limit_price": 0.009},
        ],
    )
    assert out["order"]["lmtPrice"] == pytest.approx(-0.005)


def test_build_combo_per_leg_exchange_override():
    out = build_combo(
        symbol="EUR", exchange="CME", currency="USD",
        legs=[
            {"conId": 1, "side": "BUY", "qty": 1, "limit_price": 0.01, "exchange": "GLOBEX"},
            {"conId": 2, "side": "SELL", "qty": 1, "limit_price": 0.01},
        ],
    )
    legs = out["contract"]["comboLegs"]
    assert legs[0]["exchange"] == "GLOBEX"   # per-leg override
    assert legs[1]["exchange"] == "CME"      # falls back to combo default


def test_build_combo_rejects_single_leg():
    with pytest.raises(ValueError):
        build_combo(symbol="EUR", exchange="CME", currency="USD",
                    legs=[{"conId": 1, "side": "BUY", "qty": 1, "limit_price": 0.01}])


def test_build_combo_rejects_bad_side():
    with pytest.raises(ValueError):
        build_combo(
            symbol="EUR", exchange="CME", currency="USD",
            legs=[
                {"conId": 1, "side": "HOLD", "qty": 1, "limit_price": 0.01},
                {"conId": 2, "side": "SELL", "qty": 1, "limit_price": 0.01},
            ],
        )


def test_build_combo_rejects_nonpositive_qty():
    with pytest.raises(ValueError):
        build_combo(
            symbol="EUR", exchange="CME", currency="USD",
            legs=[
                {"conId": 1, "side": "BUY", "qty": 0, "limit_price": 0.01},
                {"conId": 2, "side": "SELL", "qty": 1, "limit_price": 0.01},
            ],
        )
