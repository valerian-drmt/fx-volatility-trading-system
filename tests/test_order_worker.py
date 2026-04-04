from threading import RLock
from types import SimpleNamespace

import pytest
from ib_insync import LimitOrder, MarketOrder

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
        cancel_all_result=None,
    ):
        self.connected = connected
        self.qualified_contract = qualified_contract
        self.place_order_result = place_order_result
        self.what_if_result = what_if_result
        self.bracket_orders = bracket_orders if bracket_orders is not None else []
        self.cancel_all_result = cancel_all_result if cancel_all_result is not None else (True, 0, "No open orders to cancel.")
        self.place_order_calls = []
        self.what_if_calls = []
        self.bracket_build_calls = []
        self.cancel_all_calls = 0
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

    def build_bracket_orders(self, *, side, quantity, limit_price, take_profit_price, stop_loss_price):
        self.bracket_build_calls.append(
            {
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }
        )
        return self.bracket_orders

    def cancel_all_open_orders(self):
        self.cancel_all_calls += 1
        return self.cancel_all_result


def _build_worker(ib_client):
    worker = OrderWorker(ib_client=ib_client, io_lock=RLock())
    results = []
    failures = []
    worker.order_result.connect(results.append)
    worker.failed.connect(failures.append)
    return worker, results, failures


@pytest.mark.unit
def test_normalize_request_returns_none_for_non_dict():
    assert OrderWorker._normalize_request("bad payload") is None


@pytest.mark.unit
def test_normalize_request_parses_symbol_and_numbers():
    normalized = OrderWorker._normalize_request(
        {
            "symbol": " eur/usd ",
            "side": " buy ",
            "order_type": " mkt ",
            "quantity": "10000",
            "limit_price": "1.2345",
            "take_profit": "1.2500",
            "stop_loss": "1.2000",
        }
    )
    assert normalized == {
        "symbol": "EURUSD",
        "side": "BUY",
        "order_type": "MKT",
        "quantity": 10000,
        "limit_price": 1.2345,
        "take_profit": 1.25,
        "stop_loss": 1.2,
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
            "take_profit": None,
            "stop_loss": None,
        }
    )
    assert error == "Limit price must be > 0 for LMT orders."


@pytest.mark.unit
def test_validate_request_requires_tp_and_sl_together():
    error = OrderWorker._validate_request(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "LMT",
            "quantity": 1000,
            "limit_price": 1.1,
            "take_profit": 1.2,
            "stop_loss": None,
        }
    )
    assert error == "Set both TP and SL, or leave both empty."


@pytest.mark.unit
def test_validate_request_rejects_tp_sl_for_market_order():
    error = OrderWorker._validate_request(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "MKT",
            "quantity": 1000,
            "limit_price": 0.0,
            "take_profit": 1.2,
            "stop_loss": 1.0,
        }
    )
    assert error == "TP/SL is currently supported only for LMT orders."


@pytest.mark.unit
def test_place_order_rejects_when_worker_is_stopped(qapp):
    worker, results, failures = _build_worker(FakeIBClient())
    worker.place_order({"symbol": "EURUSD", "side": "BUY", "order_type": "MKT", "quantity": 1000})

    assert failures == []
    assert results[-1] == {"ok": False, "kind": "order", "message": "Order worker is stopped."}


@pytest.mark.unit
def test_place_order_rejects_when_not_connected(qapp):
    worker, results, failures = _build_worker(FakeIBClient(connected=False))
    worker.start()
    worker.place_order({"symbol": "EURUSD", "side": "BUY", "order_type": "MKT", "quantity": 1000})

    assert failures == []
    assert results[-1]["ok"] is False
    assert results[-1]["message"] == "Not connected to IBKR."


@pytest.mark.unit
def test_place_order_success_uses_qualified_contract(qapp):
    qualified_contract = object()
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=qualified_contract,
        place_order_result=object(),
    )
    worker, results, failures = _build_worker(fake_client)
    worker.start()

    worker.place_order(
        {
            "symbol": "eur/usd",
            "side": "BUY",
            "order_type": "MKT",
            "quantity": 25000,
            "limit_price": 0,
        }
    )

    assert failures == []
    assert results[-1]["ok"] is True
    assert results[-1]["kind"] == "order"
    assert "Order sent:" in results[-1]["message"]

    sent_contract, sent_order = fake_client.place_order_calls[-1]
    assert sent_contract is qualified_contract
    assert isinstance(sent_order, MarketOrder)
    assert sent_order.action == "BUY"
    assert int(sent_order.totalQuantity) == 25000


@pytest.mark.unit
def test_place_order_bracket_lmt_places_parent_tp_sl(qapp):
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=object(),
        place_order_result=object(),
        bracket_orders=["parent", "tp", "sl"],
    )
    worker, results, failures = _build_worker(fake_client)
    worker.start()

    worker.place_order(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "LMT",
            "quantity": 1000,
            "limit_price": 1.1,
            "take_profit": 1.12,
            "stop_loss": 1.09,
        }
    )

    assert failures == []
    assert results[-1]["ok"] is True
    assert results[-1]["kind"] == "order"
    assert "Bracket sent:" in results[-1]["message"]
    assert len(fake_client.bracket_build_calls) == 1
    assert len(fake_client.place_order_calls) == 3


@pytest.mark.unit
def test_place_order_bracket_rejects_when_build_fails(qapp):
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=object(),
        place_order_result=object(),
        bracket_orders=[],
    )
    worker, results, failures = _build_worker(fake_client)
    worker.start()

    worker.place_order(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "LMT",
            "quantity": 1000,
            "limit_price": 1.1,
            "take_profit": 1.12,
            "stop_loss": 1.09,
        }
    )

    assert failures == []
    assert results[-1]["ok"] is False
    assert "Bracket build failed" in results[-1]["message"]


@pytest.mark.unit
def test_preview_order_success_includes_margin_fields(qapp):
    what_if = SimpleNamespace(initMarginChange="10", maintMarginChange="7", commission="0.2")
    fake_client = FakeIBClient(
        connected=True,
        qualified_contract=object(),
        what_if_result=what_if,
    )
    worker, results, failures = _build_worker(fake_client)
    worker.start()

    worker.preview_order(
        {
            "symbol": "EURUSD",
            "side": "SELL",
            "order_type": "LMT",
            "quantity": 1000,
            "limit_price": 1.11111,
        }
    )

    assert failures == []
    assert results[-1]["ok"] is True
    assert results[-1]["kind"] == "preview"
    assert "InitMargin: 10" in results[-1]["message"]
    assert "Commission: 0.2" in results[-1]["message"]
    _, sent_order = fake_client.what_if_calls[-1]
    assert isinstance(sent_order, LimitOrder)


@pytest.mark.unit
def test_preview_order_returns_error_when_api_returns_none(qapp):
    fake_client = FakeIBClient(connected=True, qualified_contract=object(), what_if_result=None)
    worker, results, failures = _build_worker(fake_client)
    worker.start()

    worker.preview_order(
        {
            "symbol": "EURUSD",
            "side": "SELL",
            "order_type": "MKT",
            "quantity": 2000,
            "limit_price": 0,
        }
    )

    assert failures == []
    assert results[-1]["ok"] is False
    assert results[-1]["kind"] == "preview"
    assert "Preview failed" in results[-1]["message"]


@pytest.mark.unit
def test_cancel_all_orders_success(qapp):
    fake_client = FakeIBClient(connected=True, cancel_all_result=(True, 2, "Cancelled 2 open orders."))
    worker, results, failures = _build_worker(fake_client)
    worker.start()

    worker.cancel_all_orders({})

    assert failures == []
    assert results[-1]["ok"] is True
    assert results[-1]["kind"] == "cancel_all"
    assert results[-1]["cancelled_count"] == 2
    assert fake_client.cancel_all_calls == 1


@pytest.mark.unit
def test_cancel_all_orders_rejects_when_not_connected(qapp):
    fake_client = FakeIBClient(connected=False)
    worker, results, failures = _build_worker(fake_client)
    worker.start()

    worker.cancel_all_orders({})

    assert failures == []
    assert results[-1]["ok"] is False
    assert results[-1]["kind"] == "cancel_all"
    assert "Not connected to IBKR." in results[-1]["message"]
