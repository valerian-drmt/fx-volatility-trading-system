from types import SimpleNamespace

import pytest
from ib_insync import LimitOrder, MarketOrder, StopOrder

from services.ib_client import IBClient


class BasicIB:
    def __init__(self, connected=True):
        self._connected = connected

    def isConnected(self):
        return self._connected

    def sleep(self, seconds=0):
        pass


class DummyEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self

    def emit(self, *args):
        for handler in list(self.handlers):
            handler(*args)


@pytest.mark.unit
def test_get_environment_maps_known_ports():
    ib = BasicIB()
    assert IBClient(ib=ib, port=4002).get_environment() == "paper"
    assert IBClient(ib=ib, port=7497).get_environment() == "paper"
    assert IBClient(ib=ib, port=4001).get_environment() == "live"
    assert IBClient(ib=ib, port=7496).get_environment() == "live"
    assert IBClient(ib=ib, port=1234).get_environment() == "unknown"


@pytest.mark.unit
def test_connect_is_noop_when_already_connected():
    class AlreadyConnectedIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.connect_calls = 0

        def connect(self, *args, **kwargs):
            self.connect_calls += 1
            return True

    ib = AlreadyConnectedIB()
    client = IBClient(ib=ib)

    assert client.connect(timeout=1.0) is True
    assert ib.connect_calls == 0


@pytest.mark.unit
def test_get_latest_bid_ask_reuses_previous_valid_prices():
    ib = BasicIB(connected=True)
    client = IBClient(ib=ib)

    client.ticker = SimpleNamespace(bid=1.1001, ask=1.1004)
    assert client.get_latest_bid_ask() == (1.1001, 1.1004)

    client.ticker = SimpleNamespace(bid=float("nan"), ask=None)
    assert client.get_latest_bid_ask() == (1.1001, 1.1004)


@pytest.mark.unit
def test_request_account_summary_falls_back_to_legacy_signature():
    class AccountSummaryIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.calls = []

        def reqAccountSummary(self, *args):
            self.calls.append(args)
            if args:
                raise TypeError("legacy signature")
            return True

    ib = AccountSummaryIB()
    client = IBClient(ib=ib)

    ok = client.request_account_summary("All", "NetLiquidation,AvailableFunds")

    assert ok is True
    assert ib.calls[0] == ("All", "NetLiquidation,AvailableFunds")
    assert ib.calls[1] == ()


@pytest.mark.unit
def test_cancel_order_uses_nested_order_fallback():
    class CancelIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.calls = []

        def cancelOrder(self, order):
            self.calls.append(order)
            if len(self.calls) == 1:
                raise RuntimeError("first call failed")
            return True

    ib = CancelIB()
    client = IBClient(ib=ib)
    trade = SimpleNamespace(order="ORDER-123")

    ok = client.cancel_order(trade)

    assert ok is True
    assert ib.calls == [trade, "ORDER-123"]


@pytest.mark.unit
def test_get_status_snapshot_builds_expected_payload():
    class StatusIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.client = SimpleNamespace(readonly=True)

        def managedAccounts(self):
            return ["DU999999"]

    client = IBClient(ib=StatusIB(), client_id=42, port=4002)
    payload = client.get_status_snapshot()

    assert payload == {
        "connected": True,
        "mode": "read-only",
        "env": "paper",
        "client_id": "42",
        "account": "DU999999",
    }


@pytest.mark.unit
def test_build_bracket_orders_calls_ib_helper():
    class BracketIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.calls = []

        def bracketOrder(self, side, quantity, limit_price, take_profit_price, stop_loss_price):
            self.calls.append((side, quantity, limit_price, take_profit_price, stop_loss_price))
            return ["parent", "tp", "sl"]

    ib = BracketIB()
    client = IBClient(ib=ib)
    orders = client.build_bracket_orders(
        side="BUY",
        quantity=1000,
        limit_price=1.1,
        take_profit_price=1.12,
        stop_loss_price=1.09,
    )

    assert orders == ["parent", "tp", "sl"]
    assert ib.calls == [("BUY", 1000, 1.1, 1.12, 1.09)]


@pytest.mark.unit
def test_build_bracket_orders_mkt_parent_builds_manual_bracket_orders():
    class MktBracketIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.calls = []
            next_req_id = {"value": 10}

            def _next_id():
                next_req_id["value"] += 1
                return next_req_id["value"]

            self.client = SimpleNamespace(getReqId=_next_id)

        def bracketOrder(self, *_args, **_kwargs):
            self.calls.append("unexpected")
            return []

    ib = MktBracketIB()
    client = IBClient(ib=ib)
    orders = client.build_bracket_orders(
        side="SELL",
        quantity=5000,
        limit_price=1.2,
        take_profit_price=1.194,
        stop_loss_price=1.203,
        parent_order_type="MKT",
    )

    assert len(orders) == 3
    assert isinstance(orders[0], MarketOrder)
    assert isinstance(orders[1], LimitOrder)
    assert isinstance(orders[2], StopOrder)
    assert orders[0].action == "SELL"
    assert int(orders[0].totalQuantity) == 5000
    assert orders[0].transmit is False
    assert orders[1].action == "BUY"
    assert int(orders[1].totalQuantity) == 5000
    assert orders[1].lmtPrice == pytest.approx(1.194)
    assert orders[1].parentId == orders[0].orderId
    assert orders[1].transmit is False
    assert orders[2].action == "BUY"
    assert int(orders[2].totalQuantity) == 5000
    assert orders[2].auxPrice == pytest.approx(1.203)
    assert orders[2].parentId == orders[0].orderId
    assert orders[2].transmit is True
    assert ib.calls == []


@pytest.mark.unit
def test_request_market_data_retries_with_delayed_mode_on_competing_session():
    class MarketDataIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.errorEvent = DummyEvent()
            self.market_data_type_calls = []
            self.cancel_calls = []
            self.req_calls = 0

        def reqMarketDataType(self, market_data_type):
            self.market_data_type_calls.append(int(market_data_type))
            return True

        def cancelMktData(self, contract):
            self.cancel_calls.append(contract)
            return True

        def reqMktData(self, contract, generic_tick_list, snapshot, regulatory_snapshot):
            self.req_calls += 1
            if self.req_calls == 1:
                self.errorEvent.emit(4, 10197, "No market data during competing live session", contract)
            return SimpleNamespace(
                contract=contract,
                bid=1.1,
                ask=1.2,
                last=None,
                close=None,
                volume=None,
                time=None,
                req_calls=self.req_calls,
            )

    ib = MarketDataIB()
    client = IBClient(ib=ib)
    ticker = client.request_market_data("EURUSD")

    assert ticker is not None
    assert ticker.req_calls == 2
    assert ib.market_data_type_calls == [3, 1]
    assert ib.cancel_calls == ["EURUSD"]
    assert client.get_last_error_text() == ""


@pytest.mark.unit
def test_cancel_all_open_orders_cancels_each_open_order():
    class CancelAllIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)
            self.cancel_calls = []

        def openOrders(self):
            return ["ORDER-1", "ORDER-2"]

        def cancelOrder(self, order):
            self.cancel_calls.append(order)
            return True

    client = IBClient(ib=CancelAllIB())
    ok, cancelled_count, message = client.cancel_all_open_orders()

    assert ok is True
    assert cancelled_count == 2
    assert "Cancelled 2 open orders." in message


@pytest.mark.unit
def test_cancel_all_open_orders_requires_connection():
    client = IBClient(ib=BasicIB(connected=False))
    ok, cancelled_count, message = client.cancel_all_open_orders()

    assert ok is False
    assert cancelled_count == 0
    assert message == "Not connected to IBKR."



@pytest.mark.unit
def test_get_recent_fills_snapshot_merges_execution_and_session_fills_without_duplicates():
    class FillsIB(BasicIB):
        def __init__(self):
            super().__init__(connected=True)

        @staticmethod
        def reqExecutions():
            return [
                {"execId": "EXEC-1", "symbol": "EUR.USD", "side": "BOT", "shares": 1000, "price": 1.1},
            ]

        @staticmethod
        def fills():
            return [
                {"execId": "EXEC-1", "symbol": "EUR.USD", "side": "BOT", "shares": 1000, "price": 1.1},
                {"execId": "EXEC-2", "symbol": "GBP.USD", "side": "SLD", "shares": 2000, "price": 1.25},
            ]

    client = IBClient(ib=FillsIB())

    fills = client.get_recent_fills_snapshot()

    assert fills == [
        {"execId": "EXEC-1", "symbol": "EUR.USD", "side": "BOT", "shares": 1000, "price": 1.1},
        {"execId": "EXEC-2", "symbol": "GBP.USD", "side": "SLD", "shares": 2000, "price": 1.25},
    ]




