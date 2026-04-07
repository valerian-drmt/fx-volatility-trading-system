from types import SimpleNamespace

import pytest
from ib_insync import Forex, LimitOrder, MarketOrder

from services.order_worker import OrderWorker


class FakeIBClient:
    def __init__(
        self,
        *,
        connected=True,
        qualified_contract=None,
        place_order_result=None,
        what_if_result=None,
        bracket_orders=None,
    ):
        self.connected = connected
        self.qualified_contract = qualified_contract
        self.place_order_result = place_order_result
        self.what_if_result = what_if_result
        self.bracket_orders = bracket_orders if bracket_orders is not None else []
        self.place_order_calls = []
        self.what_if_calls = []
        self.bracket_build_calls = []
        self._last_error_text = ""

    def is_connected(self):
        return self.connected

    def qualify_contract(self, contract):
        return self.qualified_contract

    def place_order(self, contract, order):
        self.place_order_calls.append((contract, order))
        return self.place_order_result

    def what_if_order(self, contract, order):
        self.what_if_calls.append((contract, order))
        return self.what_if_result

    def clear_last_error(self):
        self._last_error_text = ""

    def get_last_error_text(self):
        return self._last_error_text

    def build_bracket_orders(
        self,
        *,
        side,
        quantity,
        limit_price,
        take_profit_price,
        stop_loss_price,
        parent_order_type="LMT",
    ):
        self.bracket_build_calls.append(
            {
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
                "parent_order_type": parent_order_type,
            }
        )
        return self.bracket_orders


def _build_worker(ib_client):
    worker = OrderWorker(ib_client=ib_client)
    return worker


@pytest.mark.unit
def test_normalize_request_returns_none_for_non_dict():
    assert OrderWorker._normalize_request("bad payload") is None


@pytest.mark.unit
def test_normalize_request_parses_bracket_payload():
    normalized = OrderWorker._normalize_request(
        {
            "symbol": " eur/usd ",
            "side": " buy ",
            "order_type": " lmt ",
            "volume": "10000",
            "limit_price": "1.2345",
            "use_bracket": True,
            "take_profit_pct": "0.5",
            "stop_loss_pct": "0.25",
        }
    )
    assert normalized == {
        "symbol": "EURUSD",
        "side": "BUY",
        "order_type": "LMT",
        "volume": 10000,
        "quantity": 10000,
        "limit_price": 1.2345,
        "reference_price": None,
        "use_bracket": True,
        "take_profit_pct": 0.5,
        "stop_loss_pct": 0.25,
    }


@pytest.mark.unit
def test_validate_request_rejects_invalid_limit_order():
    error = OrderWorker._validate_request(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "LMT",
            "quantity": 1000,
            "limit_price": 0.0,
            "use_bracket": False,
            "take_profit_pct": None,
            "stop_loss_pct": None,
        }
    )
    assert error == "Limit price must be > 0 for LMT orders."


@pytest.mark.unit
def test_validate_request_accepts_bracket_for_market_order():
    error = OrderWorker._validate_request(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "MKT",
            "quantity": 1000,
            "limit_price": 0.0,
            "use_bracket": True,
            "take_profit_pct": 0.5,
            "stop_loss_pct": 0.25,
        }
    )
    assert error is None


@pytest.mark.unit
def test_validate_request_requires_both_bracket_percentages():
    error = OrderWorker._validate_request(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "LMT",
            "quantity": 1000,
            "limit_price": 1.1,
            "use_bracket": True,
            "take_profit_pct": 0.5,
            "stop_loss_pct": None,
        }
    )
    assert error == "Set both TP% and SL% when bracket is enabled."


@pytest.mark.unit
def test_place_order_rejects_when_worker_is_stopped(qapp):
    worker = _build_worker(FakeIBClient())
    result = worker.place_order({"symbol": "EURUSD", "side": "BUY", "order_type": "MKT", "volume": 1000})

    assert result == {"ok": False, "kind": "order", "message": "Order worker is stopped."}


@pytest.mark.unit
def test_place_order_rejects_when_not_connected(qapp):
    worker = _build_worker(FakeIBClient(connected=False))
    worker.start()
    result = worker.place_order({"symbol": "EURUSD", "side": "BUY", "order_type": "MKT", "volume": 1000})

    assert result["ok"] is False
    assert result["message"] == "Not connected to IBKR."


@pytest.mark.unit
def test_place_order_success_uses_direct_forex_contract(qapp):
    fake_client = FakeIBClient(
        connected=True,
        place_order_result=object(),
    )
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.place_order(
        {
            "symbol": "eur/usd",
            "side": "BUY",
            "order_type": "MKT",
            "volume": 25000,
            "limit_price": 0,
        }
    )

    assert result["ok"] is True
    assert result["kind"] == "order"
    assert "Order sent:" in result["message"]

    sent_contract, sent_order = fake_client.place_order_calls[-1]
    assert isinstance(sent_contract, Forex)
    assert sent_contract.symbol == "EUR"
    assert sent_contract.currency == "USD"
    assert isinstance(sent_order, MarketOrder)
    assert sent_order.action == "BUY"
    assert int(sent_order.totalQuantity) == 25000
    assert sent_order.tif == "GTC"


@pytest.mark.unit
def test_place_order_does_not_call_contract_qualification(qapp):
    class _QualifyFailsClient(FakeIBClient):
        def qualify_contract(self, contract):
            raise RuntimeError("should not be called by place_order")

    fake_client = _QualifyFailsClient(connected=True, place_order_result=object())
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.place_order(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "MKT",
            "volume": 25000,
            "limit_price": 0,
        }
    )

    assert result["ok"] is True
    assert len(fake_client.place_order_calls) == 1


@pytest.mark.unit
def test_place_order_rejects_when_ib_trade_status_is_cancelled(qapp):
    rejected_trade = SimpleNamespace(orderStatus=SimpleNamespace(status="Cancelled"))
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=object(),
        place_order_result=rejected_trade,
    )
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.place_order(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "MKT",
            "volume": 1000,
            "limit_price": 0,
        }
    )

    assert result["ok"] is False
    assert "Order rejected" in result["message"]


@pytest.mark.unit
def test_place_order_bracket_lmt_places_parent_tp_sl(qapp):
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=object(),
        place_order_result=object(),
        bracket_orders=["parent", "tp", "sl"],
    )
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.place_order(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "LMT",
            "volume": 1000,
            "limit_price": 1.1,
            "use_bracket": True,
            "take_profit_pct": 0.5,
            "stop_loss_pct": 0.25,
        }
    )

    assert result["ok"] is True
    assert result["kind"] == "order"
    assert "Bracket sent:" in result["message"]
    assert len(fake_client.bracket_build_calls) == 1
    assert len(fake_client.place_order_calls) == 3
    call = fake_client.bracket_build_calls[-1]
    assert call["limit_price"] == pytest.approx(1.1)
    assert call["take_profit_price"] == pytest.approx(1.1055)
    assert call["stop_loss_price"] == pytest.approx(1.09725)
    assert call["parent_order_type"] == "LMT"


@pytest.mark.unit
def test_place_order_bracket_mkt_uses_reference_price_for_tp_sl(qapp):
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=object(),
        place_order_result=object(),
        bracket_orders=["parent", "tp", "sl"],
    )
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.place_order(
        {
            "symbol": "EURUSD",
            "side": "SELL",
            "order_type": "MKT",
            "volume": 1000,
            "limit_price": 0.0,
            "reference_price": 1.2,
            "use_bracket": True,
            "take_profit_pct": 0.5,
            "stop_loss_pct": 0.25,
        }
    )

    assert result["ok"] is True
    call = fake_client.bracket_build_calls[-1]
    assert call["limit_price"] == pytest.approx(1.2)
    assert call["take_profit_price"] == pytest.approx(1.194)
    assert call["stop_loss_price"] == pytest.approx(1.203)
    assert call["parent_order_type"] == "MKT"


@pytest.mark.unit
def test_place_order_bracket_rejects_when_build_fails(qapp):
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=object(),
        place_order_result=object(),
        bracket_orders=[],
    )
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.place_order(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "LMT",
            "volume": 1000,
            "limit_price": 1.1,
            "use_bracket": True,
            "take_profit_pct": 0.5,
            "stop_loss_pct": 0.25,
        }
    )

    assert result["ok"] is False
    assert "Bracket build failed" in result["message"]


@pytest.mark.unit
def test_preview_order_success_includes_margin_fields(qapp):
    what_if = SimpleNamespace(initMarginChange="10", maintMarginChange="7", commission="0.2")
    qualified_contract = object()
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=qualified_contract,
        what_if_result=what_if,
    )
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.preview_order(
        {
            "symbol": "EURUSD",
            "side": "SELL",
            "order_type": "LMT",
            "volume": 1000,
            "limit_price": 1.11111,
            "use_bracket": True,
            "take_profit_pct": 0.5,
            "stop_loss_pct": 0.25,
        }
    )

    assert result["ok"] is True
    assert result["kind"] == "preview"
    assert "Init Margin: 10" in result["message"]
    assert "Commission: 0.2" in result["message"]
    assert "Take Profit:" in result["message"]
    sent_contract, sent_order = fake_client.what_if_calls[-1]
    assert sent_contract is qualified_contract
    assert isinstance(sent_order, LimitOrder)
    assert sent_order.tif == "DAY"


@pytest.mark.unit
def test_preview_order_returns_error_when_api_returns_none(qapp):
    fake_client = FakeIBClient(connected=True, qualified_contract=object(), what_if_result=None)
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.preview_order(
        {
            "symbol": "EURUSD",
            "side": "SELL",
            "order_type": "MKT",
            "volume": 2000,
            "limit_price": 0,
        }
    )

    assert result["ok"] is False
    assert result["kind"] == "preview"
    assert "Preview failed" in result["message"]


@pytest.mark.unit
def test_preview_order_returns_error_when_contract_qualification_fails(qapp):
    class _PreviewClient(FakeIBClient):
        def qualify_contract(self, contract):
            self._last_error_text = "qualify_contract: No contract data returned."
            return None

    what_if = SimpleNamespace(initMarginChange="10", maintMarginChange="7", commission="0.2")
    fake_client = _PreviewClient(connected=True, what_if_result=what_if)
    worker = _build_worker(fake_client)
    worker.start()

    result = worker.preview_order(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "MKT",
            "volume": 10000,
            "limit_price": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["kind"] == "preview"
    assert "qualify_contract" in result["message"]
    assert fake_client.what_if_calls == []
