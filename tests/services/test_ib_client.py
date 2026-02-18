import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[2] / "src"))


def test_ib_client_importable():
    import services.ib_client  # noqa: F401


class FakeIB:
    def __init__(self):
        self.connected = False
        self.connect_calls = []
        self.raise_type_error_on_timeout = False
        self._type_error_raised = False
        self.ticker = SimpleNamespace(bid=None, ask=None)
        self.snapshot_ticker = SimpleNamespace(
            bid=1.1010,
            ask=1.1012,
            last=1.1011,
            close=1.1000,
            volume=12345,
            time="2026-02-14 15:30:00",
        )
        self.req_mkt_data_calls = 0
        self.last_req_mkt_data_args = None
        self.req_account_summary_calls = 0
        self.req_account_summary_args = []
        self.sleep_calls = []
        self.account_summary_result = [{"tag": "NetLiquidation", "value": "100000"}]
        self.positions_result = [{"symbol": "EURUSD", "position": 1}]
        self.raise_positions = False
        self.cancel_account_summary_called = False
        self.client = SimpleNamespace(readonly=True)
        self.accounts = ["DU123456"]
        self.current_time_result = datetime(2026, 2, 14, 15, 30, 45)
        self.raise_current_time = False
        self.historical_bars_result = [{"open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15}]
        self.head_timestamp_result = "20240101-00:00:00"
        self.qualified_contracts = [SimpleNamespace(conId=1001, symbol="EURUSD")]
        self.contract_details_result = [SimpleNamespace(longName="Euro Fx")]
        self.account_values_result = [SimpleNamespace(tag="NetLiquidation", value="100000")]
        self.open_orders_result = [SimpleNamespace(orderId=1)]
        self.fills_result = [SimpleNamespace(execId="abc")]
        self.executions_result = [SimpleNamespace(execId="xyz")]
        self.pnl_result = SimpleNamespace(dailyPnL=12.3, unrealizedPnL=4.5, realizedPnL=7.8)
        self.pnl_single_result = SimpleNamespace(position=1, dailyPnL=1.2)
        self.depth_ticker = SimpleNamespace(
            domBids=[SimpleNamespace(price=1.1000, size=100000, marketMaker="MM1")],
            domAsks=[SimpleNamespace(price=1.1002, size=120000, marketMaker="MM2")],
        )
        self.cancel_mkt_depth_calls = []
        self.cancel_mkt_data_calls = []
        self.cancel_order_calls = []
        self.place_order_result = SimpleNamespace(orderId=101, status="Submitted")
        self.what_if_result = SimpleNamespace(
            initMarginChange="100",
            maintMarginChange="80",
            commission="1.50",
        )
        self.completed_orders_result = [SimpleNamespace(orderId=5, status="Filled")]
        self.open_trades_result = [SimpleNamespace(order=SimpleNamespace(orderId=77))]
        self.req_all_open_orders_calls = 0
        self.req_completed_orders_calls = []
        self.last_place_order_args = None
        self.last_what_if_order_args = None
        self.raise_mkt_data = False
        self.raise_req_account_summary = False
        self.raise_historical = False
        self.raise_head_timestamp = False
        self.raise_qualify = False
        self.raise_contract_details = False
        self.raise_account_values = False
        self.raise_open_orders = False
        self.raise_fills = False
        self.raise_executions = False
        self.raise_pnl = False
        self.raise_pnl_single = False
        self.raise_mkt_depth = False
        self.raise_cancel_mkt_data = False
        self.raise_place_order = False
        self.raise_cancel_order = False
        self.raise_what_if_order = False
        self.raise_req_all_open_orders = False
        self.raise_req_completed_orders = False
        self.raise_open_trades = False

    def connect(self, host, port, clientId, readonly, timeout=None):
        self.connect_calls.append(
            {
                "host": host,
                "port": port,
                "clientId": clientId,
                "readonly": readonly,
                "timeout": timeout,
            }
        )
        if self.raise_type_error_on_timeout and timeout is not None and not self._type_error_raised:
            self._type_error_raised = True
            raise TypeError("timeout not supported")
        self.connected = True

    def isConnected(self):
        return self.connected

    def reqMktData(self, contract, genericTickList="", snapshot=False, regulatorySnapshot=False):
        self.req_mkt_data_calls += 1
        self.last_contract = contract
        self.last_req_mkt_data_args = (genericTickList, snapshot, regulatorySnapshot)
        if self.raise_mkt_data:
            raise RuntimeError("market data unavailable")
        if snapshot:
            return self.snapshot_ticker
        return self.ticker

    def reqAccountSummary(self, groupName=None, tags=None):
        if self.raise_req_account_summary:
            raise RuntimeError("account summary unavailable")
        self.req_account_summary_calls += 1
        self.req_account_summary_args.append({"groupName": groupName, "tags": tags})

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)

    def accountSummary(self):
        return self.account_summary_result

    def positions(self):
        if self.raise_positions:
            raise RuntimeError("positions unavailable")
        return self.positions_result

    def cancelAccountSummary(self):
        self.cancel_account_summary_called = True

    def managedAccounts(self):
        return self.accounts

    def reqCurrentTime(self):
        if self.raise_current_time:
            raise RuntimeError("current time unavailable")
        return self.current_time_result

    def reqHistoricalData(
        self,
        contract,
        endDateTime,
        durationStr,
        barSizeSetting,
        whatToShow,
        useRTH,
        formatDate,
        keepUpToDate,
    ):
        if self.raise_historical:
            raise RuntimeError("historical unavailable")
        self.last_historical_args = {
            "endDateTime": endDateTime,
            "durationStr": durationStr,
            "barSizeSetting": barSizeSetting,
            "whatToShow": whatToShow,
            "useRTH": useRTH,
            "formatDate": formatDate,
            "keepUpToDate": keepUpToDate,
        }
        return self.historical_bars_result

    def reqHeadTimeStamp(self, contract, whatToShow, useRTH, formatDate):
        if self.raise_head_timestamp:
            raise RuntimeError("head timestamp unavailable")
        return self.head_timestamp_result

    def qualifyContracts(self, contract):
        if self.raise_qualify:
            raise RuntimeError("qualify unavailable")
        return self.qualified_contracts

    def reqContractDetails(self, contract):
        if self.raise_contract_details:
            raise RuntimeError("details unavailable")
        return self.contract_details_result

    def accountValues(self):
        if self.raise_account_values:
            raise RuntimeError("account values unavailable")
        return self.account_values_result

    def openOrders(self):
        if self.raise_open_orders:
            raise RuntimeError("open orders unavailable")
        return self.open_orders_result

    def fills(self):
        if self.raise_fills:
            raise RuntimeError("fills unavailable")
        return self.fills_result

    def reqExecutions(self):
        if self.raise_executions:
            raise RuntimeError("executions unavailable")
        return self.executions_result

    def reqPnL(self, account, modelCode=""):
        if self.raise_pnl:
            raise RuntimeError("pnl unavailable")
        self.last_pnl_args = {"account": account, "modelCode": modelCode}
        return self.pnl_result

    def reqPnLSingle(self, account, modelCode="", conId=0):
        if self.raise_pnl_single:
            raise RuntimeError("pnl single unavailable")
        self.last_pnl_single_args = {"account": account, "modelCode": modelCode, "conId": conId}
        return self.pnl_single_result

    def reqMktDepth(self, contract, numRows=5):
        if self.raise_mkt_depth:
            raise RuntimeError("depth unavailable")
        self.last_mkt_depth_args = {"contract": contract, "numRows": numRows}
        return self.depth_ticker

    def cancelMktDepth(self, contract):
        self.cancel_mkt_depth_calls.append(contract)

    def cancelMktData(self, contract):
        if self.raise_cancel_mkt_data:
            raise RuntimeError("cancel market data unavailable")
        self.cancel_mkt_data_calls.append(contract)

    def placeOrder(self, contract, order):
        if self.raise_place_order:
            raise RuntimeError("place order unavailable")
        self.last_place_order_args = {"contract": contract, "order": order}
        return self.place_order_result

    def cancelOrder(self, trade_or_order):
        if self.raise_cancel_order:
            raise RuntimeError("cancel order unavailable")
        self.cancel_order_calls.append(trade_or_order)

    def whatIfOrder(self, contract, order):
        if self.raise_what_if_order:
            raise RuntimeError("what if unavailable")
        self.last_what_if_order_args = {"contract": contract, "order": order}
        return self.what_if_result

    def reqAllOpenOrders(self):
        if self.raise_req_all_open_orders:
            raise RuntimeError("req all open orders unavailable")
        self.req_all_open_orders_calls += 1

    def reqCompletedOrders(self, apiOnly=False):
        if self.raise_req_completed_orders:
            raise RuntimeError("req completed orders unavailable")
        self.req_completed_orders_calls.append(apiOnly)
        return self.completed_orders_result

    def openTrades(self):
        if self.raise_open_trades:
            raise RuntimeError("open trades unavailable")
        return self.open_trades_result


def test_connect_and_prepare_calls_ib_with_expected_args():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib, host="127.0.0.1", port=4002, client_id=7, readonly=True)

    client.connect_and_prepare(ticker="", timeout=1.5)

    assert ib.connected is True
    assert ib.connect_calls[-1] == {
        "host": "127.0.0.1",
        "port": 4002,
        "clientId": 7,
        "readonly": True,
        "timeout": 1.5,
    }


def test_connect_and_prepare_falls_back_when_timeout_not_supported():
    from services.ib_client import IBClient

    ib = FakeIB()
    ib.raise_type_error_on_timeout = True
    client = IBClient(ib=ib)

    client.connect_and_prepare(ticker="", timeout=1.0)

    assert len(ib.connect_calls) == 2
    assert ib.connect_calls[0]["timeout"] == 1.0
    assert ib.connect_calls[1]["timeout"] is None


def test_connect_and_prepare_sets_ticker_and_requests_summary():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib, ticker=None)

    ticker = client.connect_and_prepare()

    assert ticker is ib.ticker
    assert client.ticker is ib.ticker
    assert ib.req_mkt_data_calls == 1
    assert ib.req_account_summary_calls == 1


def test_connect_and_prepare_keeps_existing_ticker():
    from services.ib_client import IBClient

    ib = FakeIB()
    existing = SimpleNamespace(bid=1.1, ask=1.2)
    client = IBClient(ib=ib, ticker=existing)

    ticker = client.connect_and_prepare()

    assert ticker is existing
    assert ib.req_mkt_data_calls == 0
    assert ib.req_account_summary_calls == 1


def test_is_connected_reflects_ib_state():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.is_connected() is False
    ib.connected = True
    assert client.is_connected() is True


def test_process_messages_calls_sleep_zero():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)

    client.process_messages()

    assert ib.sleep_calls == [0]


def test_is_valid_price_checks_none_nan_and_number():
    from services.ib_client import IBClient

    assert IBClient._is_valid_price(None) is False
    assert IBClient._is_valid_price(float("nan")) is False
    assert IBClient._is_valid_price(1.2345) is True


def test_get_latest_bid_ask_requires_connection_and_ticker():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib, ticker=None)
    assert client.get_latest_bid_ask() == (None, None)

    ib.connected = True
    assert client.get_latest_bid_ask() == (None, None)


def test_get_latest_bid_ask_uses_last_valid_values():
    from services.ib_client import IBClient

    ib = FakeIB()
    ib.connected = True
    ib.ticker.bid = 1.1000
    ib.ticker.ask = 1.1002
    client = IBClient(ib=ib, ticker=ib.ticker)

    assert client.get_latest_bid_ask() == (1.1000, 1.1002)

    ib.ticker.bid = float("nan")
    ib.ticker.ask = 1.1003
    assert client.get_latest_bid_ask() == (1.1000, 1.1003)


def test_get_portfolio_snapshot_connected_and_disconnected_paths():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)

    assert client.get_portfolio_snapshot() == ([], [])

    ib.connected = True
    summary, positions = client.get_portfolio_snapshot()
    assert summary == ib.account_summary_result
    assert positions == ib.positions_result


def test_get_portfolio_snapshot_handles_positions_exception():
    from services.ib_client import IBClient

    ib = FakeIB()
    ib.connected = True
    ib.raise_positions = True
    client = IBClient(ib=ib)

    summary, positions = client.get_portfolio_snapshot()

    assert summary == ib.account_summary_result
    assert positions == []


def test_cancel_account_summary_calls_ib_method():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)

    client.cancel_account_summary()

    assert ib.cancel_account_summary_called is True


def test_get_connection_mode_values():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.get_connection_mode() == "read-only"

    ib.client.readonly = False
    assert client.get_connection_mode() == "read-write"

    ib.client = None
    assert client.get_connection_mode() == "unknown"


def test_get_environment_port_mapping():
    from services.ib_client import IBClient

    assert IBClient(ib=FakeIB(), port=4002).get_environment() == "paper"
    assert IBClient(ib=FakeIB(), port=7497).get_environment() == "paper"
    assert IBClient(ib=FakeIB(), port=4001).get_environment() == "live"
    assert IBClient(ib=FakeIB(), port=7496).get_environment() == "live"
    assert IBClient(ib=FakeIB(), port=1234).get_environment() == "unknown"


def test_get_account_returns_first_or_default():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.get_account() == "DU123456"

    ib.accounts = []
    assert client.get_account() == "--"


def test_supports_server_time():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.supports_server_time() is True

    ib_no_time = SimpleNamespace()
    client_no_time = IBClient(ib=ib_no_time)
    assert client_no_time.supports_server_time() is False


def test_get_server_time_and_latency_success(monkeypatch):
    import services.ib_client as ib_client_module
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)

    time_values = iter([100.0, 100.025])
    monkeypatch.setattr(ib_client_module.time, "time", lambda: next(time_values))

    server_time, latency = client.get_server_time_and_latency()

    assert server_time == "15:30:45"
    assert latency == "25 ms"


def test_get_server_time_and_latency_handles_unsupported_or_exception():
    from services.ib_client import IBClient

    ib_no_time = SimpleNamespace()
    client_no_time = IBClient(ib=ib_no_time)
    assert client_no_time.get_server_time_and_latency() == ("--", "--")

    ib = FakeIB()
    ib.raise_current_time = True
    client = IBClient(ib=ib)
    assert client.get_server_time_and_latency() == ("--", "--")


def test_get_status_snapshot_aggregates_fields():
    from services.ib_client import IBClient

    ib = FakeIB()
    ib.connected = True
    ib.client.readonly = False
    ib.accounts = ["DU999999"]
    client = IBClient(ib=ib, client_id=42, port=4001)

    status = client.get_status_snapshot()

    assert status == {
        "connected": True,
        "mode": "read-write",
        "env": "live",
        "client_id": "42",
        "account": "DU999999",
    }


def test_get_market_snapshot_success_and_disconnected():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.get_market_snapshot(contract="EURUSD") == {}

    ib.connected = True
    snapshot = client.get_market_snapshot(contract="EURUSD", wait_seconds=0.0)
    assert snapshot["bid"] == 1.1010
    assert snapshot["ask"] == 1.1012
    assert snapshot["last"] == 1.1011
    assert snapshot["close"] == 1.1000
    assert snapshot["volume"] == 12345
    assert snapshot["time"] == "2026-02-14 15:30:00"


def test_get_historical_bars_success_and_failure():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.get_historical_bars(contract="EURUSD") == []

    ib.connected = True
    bars = client.get_historical_bars(contract="EURUSD", duration="2 D", bar_size="5 mins", what_to_show="TRADES")
    assert bars == ib.historical_bars_result
    assert ib.last_historical_args["durationStr"] == "2 D"
    assert ib.last_historical_args["barSizeSetting"] == "5 mins"
    assert ib.last_historical_args["whatToShow"] == "TRADES"

    ib.raise_historical = True
    assert client.get_historical_bars(contract="EURUSD") == []


def test_get_head_timestamp_and_qualify_contract():
    from services.ib_client import IBClient

    ib = FakeIB()
    ib.connected = True
    client = IBClient(ib=ib)

    assert client.get_head_timestamp(contract="EURUSD") == "20240101-00:00:00"
    qualified = client.qualify_contract(contract="EURUSD")
    assert qualified.conId == 1001

    ib.qualified_contracts = []
    assert client.qualify_contract(contract="EURUSD") is None


def test_get_contract_details_and_account_values():
    from services.ib_client import IBClient

    ib = FakeIB()
    ib.connected = True
    client = IBClient(ib=ib)

    assert client.get_contract_details(contract="EURUSD") == ib.contract_details_result
    assert client.get_account_values() == ib.account_values_result

    ib.raise_contract_details = True
    ib.raise_account_values = True
    assert client.get_contract_details(contract="EURUSD") == []
    assert client.get_account_values() == []


def test_get_orders_fills_executions_snapshots():
    from services.ib_client import IBClient

    ib = FakeIB()
    ib.connected = True
    client = IBClient(ib=ib)

    assert client.get_open_orders_snapshot() == ib.open_orders_result
    assert client.get_fills_snapshot() == ib.fills_result
    assert client.get_executions_snapshot() == ib.executions_result

    ib.raise_open_orders = True
    ib.raise_fills = True
    ib.raise_executions = True
    assert client.get_open_orders_snapshot() == []
    assert client.get_fills_snapshot() == []
    assert client.get_executions_snapshot() == []


def test_get_pnl_and_pnl_single_snapshots():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.get_pnl_snapshot(account="DU1") is None
    assert client.get_pnl_single_snapshot(account="DU1", con_id=1001) is None

    ib.connected = True
    pnl = client.get_pnl_snapshot(account="DU1", model_code="M1")
    pnl_single = client.get_pnl_single_snapshot(account="DU1", con_id=1001, model_code="M1")
    assert pnl is ib.pnl_result
    assert pnl_single is ib.pnl_single_result
    assert ib.last_pnl_args == {"account": "DU1", "modelCode": "M1"}
    assert ib.last_pnl_single_args == {"account": "DU1", "modelCode": "M1", "conId": 1001}
    assert ib.sleep_calls[-2:] == [0, 0]


def test_get_market_depth_snapshot_success_and_disconnected():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.get_market_depth_snapshot(contract="EURUSD") == {"bids": [], "asks": []}

    ib.connected = True
    depth = client.get_market_depth_snapshot(contract="EURUSD", num_rows=3, wait_seconds=0.0)
    assert depth["bids"][0]["price"] == 1.1000
    assert depth["asks"][0]["price"] == 1.1002
    assert ib.last_mkt_depth_args == {"contract": "EURUSD", "numRows": 3}
    assert ib.cancel_mkt_depth_calls == ["EURUSD"]


def test_request_account_summary_success_and_failure():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    assert client.request_account_summary(group_name="All", tags="NetLiquidation") is False

    ib.connected = True
    assert client.request_account_summary(group_name="All", tags="NetLiquidation,AvailableFunds") is True
    assert ib.req_account_summary_calls == 1
    assert ib.req_account_summary_args[-1] == {
        "groupName": "All",
        "tags": "NetLiquidation,AvailableFunds",
    }

    ib.raise_req_account_summary = True
    assert client.request_account_summary(group_name="All", tags="NetLiquidation") is False


def test_request_and_cancel_market_data():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)

    assert client.request_market_data(contract="EURUSD") is None
    assert client.cancel_market_data(contract="EURUSD") is False

    ib.connected = True
    ticker = client.request_market_data(
        contract="EURUSD",
        generic_tick_list="",
        snapshot=True,
        regulatory_snapshot=False,
    )
    assert ticker is ib.snapshot_ticker
    assert ib.last_req_mkt_data_args == ("", True, False)

    assert client.cancel_market_data(contract="EURUSD") is True
    assert ib.cancel_mkt_data_calls == ["EURUSD"]

    ib.raise_mkt_data = True
    ib.raise_cancel_mkt_data = True
    assert client.request_market_data(contract="EURUSD") is None
    assert client.cancel_market_data(contract="EURUSD") is False


def test_order_request_methods():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)
    order = SimpleNamespace(action="BUY", totalQuantity=1000, lmtPrice=1.0, orderId=12)
    trade = SimpleNamespace(order=order)

    assert client.place_order(contract="EURUSD", order=order) is None
    assert client.replace_order(contract="EURUSD", order_with_existing_order_id=order) is None
    assert client.cancel_order(trade_or_order=trade) is False
    assert client.what_if_order(contract="EURUSD", order=order) is None

    ib.connected = True
    placed = client.place_order(contract="EURUSD", order=order)
    assert placed is ib.place_order_result
    assert ib.last_place_order_args == {"contract": "EURUSD", "order": order}

    replaced = client.replace_order(contract="EURUSD", order_with_existing_order_id=order)
    assert replaced is ib.place_order_result

    assert client.cancel_order(trade_or_order=order) is True
    assert client.cancel_order(trade_or_order=trade) is True
    assert ib.cancel_order_calls == [order, trade]

    what_if = client.what_if_order(contract="EURUSD", order=order)
    assert what_if is ib.what_if_result
    assert ib.last_what_if_order_args == {"contract": "EURUSD", "order": order}

    ib.raise_place_order = True
    ib.raise_cancel_order = True
    ib.raise_what_if_order = True
    assert client.place_order(contract="EURUSD", order=order) is None
    assert client.replace_order(contract="EURUSD", order_with_existing_order_id=order) is None
    assert client.cancel_order(trade_or_order=order) is False
    assert client.what_if_order(contract="EURUSD", order=order) is None


def test_request_open_completed_and_open_trades_methods():
    from services.ib_client import IBClient

    ib = FakeIB()
    client = IBClient(ib=ib)

    assert client.request_all_open_orders() == []
    assert client.request_completed_orders(api_only=False) == []
    assert client.get_open_trades_snapshot() == []

    ib.connected = True
    assert client.request_all_open_orders() == ib.open_orders_result
    assert ib.req_all_open_orders_calls == 1

    completed = client.request_completed_orders(api_only=True)
    assert completed == ib.completed_orders_result
    assert ib.req_completed_orders_calls == [True]

    assert client.get_open_trades_snapshot() == ib.open_trades_result

    ib.raise_req_all_open_orders = True
    ib.raise_req_completed_orders = True
    ib.raise_open_trades = True
    assert client.request_all_open_orders() == []
    assert client.request_completed_orders(api_only=False) == []
    assert client.get_open_trades_snapshot() == []
