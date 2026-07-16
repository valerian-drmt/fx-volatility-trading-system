"""Unit tests for the spot EUR/USD (secType CASH) order path.

Why it matters: the Trade tab's Spot panel sends CASH market orders whose qty
is base-currency NOTIONAL (100_000 = 100k EUR), not contracts — so the
per-secType bounds in PlaceOrderBody and the MarketOrder fallback in
OrderExecutor.place_order are what keep a fat-fingered option order from
sneaking through the relaxed spot limits (and vice versa).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from engines.execution.main import PlaceOrderBody
from engines.execution.order_executor import OrderExecutor, OrderRequest

# ---- PlaceOrderBody per-secType bounds -----------------------------------


def test_cash_order_without_limit_is_valid() -> None:
    body = PlaceOrderBody(
        symbol="EUR", sec_type="CASH", side="BUY", qty=100_000,
        exchange="IDEALPRO", currency="USD",
    )
    assert body.limit_price is None
    assert body.qty == 100_000


def test_cash_qty_capped_at_5m_notional() -> None:
    with pytest.raises(ValidationError, match="5,000,000"):
        PlaceOrderBody(
            symbol="EUR", sec_type="CASH", side="SELL", qty=5_000_001,
            exchange="IDEALPRO", currency="USD",
        )


def test_fop_requires_limit_price() -> None:
    with pytest.raises(ValidationError, match="limit_price is required"):
        PlaceOrderBody(
            symbol="EUU", sec_type="FOP", side="BUY", qty=1,
            expiry="20261218", strike=1.10, right="C",
        )


def test_fut_keeps_contract_qty_cap() -> None:
    # The relaxed spot notional bound must NOT leak to futures/options.
    with pytest.raises(ValidationError, match="1000 contracts"):
        PlaceOrderBody(
            symbol="EUR", sec_type="FUT", side="BUY", qty=1001,
            limit_price=1.10, expiry="20261218",
        )


# ---- OrderExecutor.place_order market-order fallback ----------------------


class _FakeIB:
    """Captures the (contract, order) pair place_order routes to IB."""

    def __init__(self) -> None:
        self.placed: list[tuple[Any, Any]] = []

    def isConnected(self) -> bool:
        return True

    async def qualifyContractsAsync(self, contract: Any) -> list[Any]:
        return [contract]

    def placeOrder(self, contract: Any, order: Any) -> Any:
        self.placed.append((contract, order))
        return SimpleNamespace(
            order=SimpleNamespace(orderId=1, permId=2, action=order.action,
                                  totalQuantity=order.totalQuantity,
                                  lmtPrice=getattr(order, "lmtPrice", 0.0)),
            contract=contract,
            orderStatus=SimpleNamespace(status="Submitted", filled=0.0,
                                        remaining=order.totalQuantity,
                                        avgFillPrice=0.0),
        )


def _executor_with(fake: _FakeIB) -> OrderExecutor:
    ex = OrderExecutor(host="x", port=1, client_id=9)
    ex._ib = fake
    return ex


def test_place_order_cash_no_limit_goes_market() -> None:
    fake = _FakeIB()
    ex = _executor_with(fake)
    req = OrderRequest(symbol="EUR", sec_type="CASH", side="BUY", qty=100_000,
                       limit_price=None, exchange="IDEALPRO", currency="USD")
    result = asyncio.run(ex.place_order(req))
    contract, order = fake.placed[0]
    assert contract.secType == "CASH"
    assert contract.exchange == "IDEALPRO"
    assert order.orderType == "MKT"
    assert result["qty"] == 100_000.0
    assert result["limit_price"] is None


def test_place_order_with_limit_stays_limit() -> None:
    fake = _FakeIB()
    ex = _executor_with(fake)
    req = OrderRequest(symbol="EUR", sec_type="CASH", side="SELL", qty=50_000,
                       limit_price=1.0850, exchange="IDEALPRO", currency="USD")
    asyncio.run(ex.place_order(req))
    _, order = fake.placed[0]
    assert order.orderType == "LMT"
    assert order.lmtPrice == 1.0850
