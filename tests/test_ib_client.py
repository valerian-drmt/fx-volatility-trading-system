from types import SimpleNamespace

import pytest

from services.ib_client import IBClient


class BasicIB:
    def __init__(self, connected=True):
        self._connected = connected

    def isConnected(self):
        return self._connected


@pytest.mark.unit
def test_get_environment_maps_known_ports():
    ib = BasicIB()
    assert IBClient(ib=ib, port=4002).get_environment() == "paper"
    assert IBClient(ib=ib, port=7497).get_environment() == "paper"
    assert IBClient(ib=ib, port=4001).get_environment() == "live"
    assert IBClient(ib=ib, port=7496).get_environment() == "live"
    assert IBClient(ib=ib, port=1234).get_environment() == "unknown"


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
