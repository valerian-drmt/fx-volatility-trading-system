import math
import time

from ib_insync import Forex, IB


class IBClient:
    def __init__(
        self,
        ib: IB,
        ticker=None,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 2,
        readonly: bool = True,
    ):
        self.ib = ib
        self.host = host
        self.port = port
        self.client_id = client_id
        self.readonly = readonly
        self.ticker = ticker
        self.last_bid = None
        self.last_ask = None

    def connect(self, timeout: float = 1.0):
        try:
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                readonly=self.readonly,
                timeout=timeout,
            )
        except TypeError:
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                readonly=self.readonly,
            )

    def connect_and_prepare(self):
        self.connect()
        if self.ticker is None:
            self.ticker = self.ib.reqMktData(Forex("EURUSD"))
        if hasattr(self.ib, "reqAccountSummary"):
            self.ib.reqAccountSummary()
        return self.ticker

    def is_connected(self) -> bool:
        return bool(self.ib.isConnected())

    def process_messages(self):
        self.ib.sleep(0)

    @staticmethod
    def _is_valid_price(value) -> bool:
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return not math.isnan(value)
        return True

    def get_latest_bid_ask(self):
        if not self.is_connected() or self.ticker is None:
            return None, None

        bid = getattr(self.ticker, "bid", None)
        ask = getattr(self.ticker, "ask", None)

        if self._is_valid_price(bid):
            self.last_bid = bid
        if self._is_valid_price(ask):
            self.last_ask = ask

        if self.last_bid is None or self.last_ask is None:
            return None, None
        return self.last_bid, self.last_ask

    def get_portfolio_snapshot(self):
        if not self.is_connected():
            return [], []

        summary = self.ib.accountSummary()
        positions = []
        if hasattr(self.ib, "positions"):
            try:
                positions = self.ib.positions()
            except Exception:
                positions = []
        return summary, positions

    def cancel_account_summary(self):
        if hasattr(self.ib, "cancelAccountSummary"):
            self.ib.cancelAccountSummary()

    def request_account_summary(self, group_name: str = "All", tags: str = "NetLiquidation,AvailableFunds") -> bool:
        if not self.is_connected() or not hasattr(self.ib, "reqAccountSummary"):
            return False
        try:
            self.ib.reqAccountSummary(group_name, tags)
            return True
        except TypeError:
            try:
                self.ib.reqAccountSummary()
                return True
            except Exception:
                return False
        except Exception:
            return False

    def get_connection_mode(self) -> str:
        client = getattr(self.ib, "client", None)
        readonly = getattr(client, "readonly", None) if client is not None else None
        if readonly is None:
            return "unknown"
        return "read-only" if readonly else "read-write"

    def get_environment(self) -> str:
        if self.port in (4002, 7497):
            return "paper"
        if self.port in (4001, 7496):
            return "live"
        return "unknown"

    def get_account(self) -> str:
        accounts = []
        if hasattr(self.ib, "managedAccounts"):
            accounts = self.ib.managedAccounts()
        return accounts[0] if accounts else "--"

    def supports_server_time(self) -> bool:
        return hasattr(self.ib, "reqCurrentTime")

    def get_server_time_and_latency(self):
        if not self.supports_server_time():
            return "--", "--"
        try:
            start = time.time()
            server_time = self.ib.reqCurrentTime()
            elapsed_ms = int((time.time() - start) * 1000)
            if isinstance(server_time, (int, float)):
                server_dt = time.localtime(server_time)
                time_text = time.strftime("%H:%M:%S", server_dt)
            else:
                time_text = server_time.strftime("%H:%M:%S")
            return time_text, f"{elapsed_ms} ms"
        except Exception:
            return "--", "--"

    def get_market_snapshot(self, contract, wait_seconds: float = 1.0):
        if not self.is_connected():
            return {}
        try:
            ticker = self.ib.reqMktData(contract, "", True, False)
            if wait_seconds > 0:
                self.ib.sleep(wait_seconds)
            return {
                "bid": getattr(ticker, "bid", None),
                "ask": getattr(ticker, "ask", None),
                "last": getattr(ticker, "last", None),
                "close": getattr(ticker, "close", None),
                "volume": getattr(ticker, "volume", None),
                "time": getattr(ticker, "time", None),
            }
        except Exception:
            return {}

    def request_market_data(
        self,
        contract,
        generic_tick_list: str = "",
        snapshot: bool = False,
        regulatory_snapshot: bool = False,
    ):
        if not self.is_connected():
            return None
        try:
            return self.ib.reqMktData(
                contract,
                generic_tick_list,
                snapshot,
                regulatory_snapshot,
            )
        except Exception:
            return None

    def cancel_market_data(self, contract) -> bool:
        if not self.is_connected() or not hasattr(self.ib, "cancelMktData"):
            return False
        try:
            self.ib.cancelMktData(contract)
            return True
        except Exception:
            return False

    def get_historical_bars(
        self,
        contract,
        duration: str = "1 D",
        bar_size: str = "1 min",
        what_to_show: str = "MIDPOINT",
        use_rth: bool = False,
    ):
        if not self.is_connected():
            return []
        try:
            return self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                keepUpToDate=False,
            )
        except Exception:
            return []

    def get_head_timestamp(self, contract, what_to_show: str = "MIDPOINT", use_rth: bool = False):
        if not self.is_connected():
            return None
        try:
            return self.ib.reqHeadTimeStamp(
                contract,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
            )
        except Exception:
            return None

    def qualify_contract(self, contract):
        if not self.is_connected():
            return None
        try:
            contracts = self.ib.qualifyContracts(contract)
            return contracts[0] if contracts else None
        except Exception:
            return None

    def get_contract_details(self, contract):
        if not self.is_connected():
            return []
        try:
            return self.ib.reqContractDetails(contract)
        except Exception:
            return []

    def get_account_values(self):
        if not self.is_connected():
            return []
        try:
            return self.ib.accountValues()
        except Exception:
            return []

    def get_open_orders_snapshot(self):
        if not self.is_connected():
            return []
        try:
            return self.ib.openOrders()
        except Exception:
            return []

    def request_all_open_orders(self):
        if not self.is_connected():
            return []
        try:
            if hasattr(self.ib, "reqAllOpenOrders"):
                self.ib.reqAllOpenOrders()
            if hasattr(self.ib, "openOrders"):
                return self.ib.openOrders()
            return []
        except Exception:
            return []

    def request_completed_orders(self, api_only: bool = False):
        if not self.is_connected() or not hasattr(self.ib, "reqCompletedOrders"):
            return []
        try:
            return self.ib.reqCompletedOrders(apiOnly=api_only)
        except TypeError:
            try:
                return self.ib.reqCompletedOrders(api_only)
            except Exception:
                return []
        except Exception:
            return []

    def get_open_trades_snapshot(self):
        if not self.is_connected() or not hasattr(self.ib, "openTrades"):
            return []
        try:
            return self.ib.openTrades()
        except Exception:
            return []

    def get_fills_snapshot(self):
        if not self.is_connected():
            return []
        try:
            return self.ib.fills()
        except Exception:
            return []

    def place_order(self, contract, order):
        if not self.is_connected() or not hasattr(self.ib, "placeOrder"):
            return None
        try:
            return self.ib.placeOrder(contract, order)
        except Exception:
            return None

    def replace_order(self, contract, order_with_existing_order_id):
        return self.place_order(contract, order_with_existing_order_id)

    def cancel_order(self, trade_or_order) -> bool:
        if not self.is_connected() or not hasattr(self.ib, "cancelOrder"):
            return False
        try:
            self.ib.cancelOrder(trade_or_order)
            return True
        except Exception:
            order_obj = getattr(trade_or_order, "order", None)
            if order_obj is None:
                return False
            try:
                self.ib.cancelOrder(order_obj)
                return True
            except Exception:
                return False

    def what_if_order(self, contract, order):
        if not self.is_connected() or not hasattr(self.ib, "whatIfOrder"):
            return None
        try:
            return self.ib.whatIfOrder(contract, order)
        except Exception:
            return None

    def get_executions_snapshot(self):
        if not self.is_connected():
            return []
        try:
            return self.ib.reqExecutions()
        except Exception:
            return []

    def get_pnl_snapshot(self, account: str, model_code: str = ""):
        if not self.is_connected():
            return None
        try:
            pnl = self.ib.reqPnL(account, modelCode=model_code)
            self.ib.sleep(0)
            return pnl
        except Exception:
            return None

    def get_pnl_single_snapshot(self, account: str, con_id: int, model_code: str = ""):
        if not self.is_connected():
            return None
        try:
            pnl_single = self.ib.reqPnLSingle(account, modelCode=model_code, conId=con_id)
            self.ib.sleep(0)
            return pnl_single
        except Exception:
            return None

    def get_market_depth_snapshot(self, contract, num_rows: int = 5, wait_seconds: float = 1.0):
        if not self.is_connected():
            return {"bids": [], "asks": []}
        try:
            ticker = self.ib.reqMktDepth(contract, numRows=num_rows)
            if wait_seconds > 0:
                self.ib.sleep(wait_seconds)
            bids = [
                {
                    "price": getattr(level, "price", None),
                    "size": getattr(level, "size", None),
                    "market_maker": getattr(level, "marketMaker", None),
                }
                for level in getattr(ticker, "domBids", [])
            ]
            asks = [
                {
                    "price": getattr(level, "price", None),
                    "size": getattr(level, "size", None),
                    "market_maker": getattr(level, "marketMaker", None),
                }
                for level in getattr(ticker, "domAsks", [])
            ]
            return {"bids": bids, "asks": asks}
        except Exception:
            return {"bids": [], "asks": []}
        finally:
            if hasattr(self.ib, "cancelMktDepth"):
                try:
                    self.ib.cancelMktDepth(contract)
                except Exception:
                    pass

    def get_status_snapshot(self):
        return {
            "connected": self.is_connected(),
            "mode": self.get_connection_mode(),
            "env": self.get_environment(),
            "client_id": str(self.client_id),
            "account": self.get_account(),
        }
