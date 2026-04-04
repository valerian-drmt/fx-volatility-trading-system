import asyncio
import inspect
import math
import time

from ib_insync import Forex, IB


class IBClient:
    def __init__(
        self,
        ib: IB,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        readonly: bool = True,
        ticker=None,
    ):
        self.ib = ib
        self.host = host
        self.port = port
        self.client_id = client_id
        self.readonly = readonly
        self.ticker = ticker
        self.last_bid = None
        self.last_ask = None
        self._received_ticks = []
        self._ticker_event_source = None
        self._last_error_context = ""
        self._last_error_message = ""

    @staticmethod
    def _resolve_maybe_awaitable(value):
        """
        Support both sync and async-style IB API variants.

        Some runtimes expose methods like `sleep()` / `accountSummary()` as coroutines.
        This helper executes awaitables in the current thread when needed.
        """
        if not inspect.isawaitable(value):
            return value
        try:
            return asyncio.run(value)
        except RuntimeError:
            # Fallback when a loop context already exists in this thread.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(value)
            finally:
                loop.close()

    def _call_ib(self, method_name: str, *args, **kwargs):
        method = getattr(self.ib, method_name, None)
        if method is None:
            raise AttributeError(f"IB client has no method '{method_name}'")
        return self._resolve_maybe_awaitable(method(*args, **kwargs))

    def clear_last_error(self):
        self._last_error_context = ""
        self._last_error_message = ""

    def _set_last_error(self, context: str, error):
        self._last_error_context = str(context or "ib").strip() or "ib"
        self._last_error_message = str(error).strip()

    def get_last_error(self) -> dict | None:
        if not self._last_error_message:
            return None
        return {
            "context": self._last_error_context or "ib",
            "message": self._last_error_message,
        }

    def get_last_error_text(self) -> str:
        payload = self.get_last_error()
        if payload is None:
            return ""
        return f"{payload['context']}: {payload['message']}"

    def connect(self, timeout: float = 1.0) -> bool:
        self.clear_last_error()
        try:
            try:
                self._resolve_maybe_awaitable(
                    self.ib.connect(
                        self.host,
                        self.port,
                        clientId=self.client_id,
                        readonly=self.readonly,
                        timeout=timeout,
                    )
                )
            except TypeError:
                self._resolve_maybe_awaitable(
                    self.ib.connect(
                        self.host,
                        self.port,
                        clientId=self.client_id,
                        readonly=self.readonly,
                    )
                )
        except Exception as exc:
            self._set_last_error("connect", exc)
            return False
        if hasattr(self.ib, "reqAccountSummary"):
            try:
                self._resolve_maybe_awaitable(self.ib.reqAccountSummary())
            except Exception:
                pass
        connected = self.is_connected()
        if connected:
            self.clear_last_error()
            return True
        self._set_last_error("connect", f"Connection to {self.host}:{self.port} failed.")
        return False

    def start_live_streaming(self, ticker: str) -> bool:
        if not self.is_connected():
            self._set_last_error("start_live_streaming", "Not connected to IBKR.")
            return False

        symbol = str(ticker).strip().upper()
        if not symbol:
            self._set_last_error("start_live_streaming", "Symbol is required.")
            return False

        self.stop_live_streaming()
        stream_ticker = self.request_market_data(Forex(symbol), snapshot=False, regulatory_snapshot=False)
        if stream_ticker is None:
            if not self.get_last_error_text():
                self._set_last_error("start_live_streaming", f"Market data subscription failed for {symbol}.")
            return False

        self.ticker = stream_ticker
        self._received_ticks = []
        self.last_bid = None
        self.last_ask = None
        self._attach_ticker_listener()
        self.clear_last_error()
        return self.ticker is not None

    def stop_live_streaming(self):
        self._detach_ticker_listener()

        contract = None
        if self.ticker is not None:
            contract = getattr(self.ticker, "contract", None)
        if contract is not None:
            self.cancel_market_data(contract)

        self.ticker = None
        self._received_ticks = []
        self.last_bid = None
        self.last_ask = None

    def connect_and_prepare(self, ticker: str = "", timeout: float = 1.0):
        self.connect(timeout=timeout)

        if self.ticker is not None:
            return self.ticker

        symbol = str(ticker).strip().upper()
        if symbol:
            self.start_live_streaming(symbol)
            return self.ticker

        # Backward-compatible default stream request when no symbol is provided.
        requested_ticker = self.request_market_data(Forex("EURUSD"), snapshot=False, regulatory_snapshot=False)
        if requested_ticker is not None:
            self.ticker = requested_ticker
            self._attach_ticker_listener()
        return self.ticker

    def is_connected(self) -> bool:
        return bool(self.ib.isConnected())

    def get_connection_state(self, connecting: bool = False) -> str:
        if self.is_connected():
            return "connected"
        if connecting:
            return "connecting"
        return "disconnected"

    def process_messages(self) -> list[dict]:
        if hasattr(self.ib, "sleep"):
            try:
                self._resolve_maybe_awaitable(self.ib.sleep(0))
            except Exception:
                pass
        return self.drain_received_ticks()

    def _attach_ticker_listener(self):
        ticker = self.ticker
        if ticker is None:
            return

        update_event = getattr(ticker, "updateEvent", None)
        if update_event is None:
            return

        if self._ticker_event_source is ticker:
            return

        self._detach_ticker_listener()
        try:
            update_event += self._on_ticker_update
            self._ticker_event_source = ticker
        except Exception:
            self._ticker_event_source = None

    def _detach_ticker_listener(self):
        if self._ticker_event_source is None:
            return

        update_event = getattr(self._ticker_event_source, "updateEvent", None)
        if update_event is None:
            self._ticker_event_source = None
            return

        try:
            update_event -= self._on_ticker_update
        except Exception:
            pass
        self._ticker_event_source = None

    @staticmethod
    def _format_tick_time(raw_time) -> str:
        if raw_time is None:
            return "--"
        if hasattr(raw_time, "strftime"):
            try:
                return raw_time.strftime("%H:%M:%S.%f")[:-3]
            except Exception:
                return str(raw_time)
        return str(raw_time)

    def _on_ticker_update(self, ticker=None, *_):
        source = ticker if ticker is not None else self.ticker
        if source is None:
            return

        self._received_ticks.append(
            {
                "time": self._format_tick_time(getattr(source, "time", None)),
                "bid": getattr(source, "bid", None),
                "ask": getattr(source, "ask", None),
                "bid_size": getattr(source, "bidSize", None),
                "ask_size": getattr(source, "askSize", None),
                "last": getattr(source, "last", None),
            }
        )

    def drain_received_ticks(self) -> list[dict]:
        ticks = self._received_ticks
        self._received_ticks = []
        return ticks

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

        summary = self._resolve_maybe_awaitable(self.ib.accountSummary())
        if summary is None:
            summary = []
        positions = []
        if hasattr(self.ib, "positions"):
            try:
                positions = self._resolve_maybe_awaitable(self.ib.positions())
                if positions is None:
                    positions = []
            except Exception:
                positions = []
        return summary, positions

    def cancel_account_summary(self):
        if hasattr(self.ib, "cancelAccountSummary"):
            try:
                self._call_ib("cancelAccountSummary")
            except Exception:
                pass

    def request_account_summary(self, group_name: str = "All", tags: str = "NetLiquidation,AvailableFunds") -> bool:
        if not self.is_connected() or not hasattr(self.ib, "reqAccountSummary"):
            return False
        try:
            self._call_ib("reqAccountSummary", group_name, tags)
            return True
        except TypeError:
            try:
                self._call_ib("reqAccountSummary")
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
            try:
                accounts = self._resolve_maybe_awaitable(self.ib.managedAccounts())
            except Exception:
                accounts = []
        return accounts[0] if accounts else "--"

    def supports_server_time(self) -> bool:
        return hasattr(self.ib, "reqCurrentTime")

    def get_server_time_and_latency(self):
        if not self.supports_server_time():
            return "--", "--"
        try:
            start = time.time()
            server_time = self._resolve_maybe_awaitable(self.ib.reqCurrentTime())
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
            ticker = self._call_ib("reqMktData", contract, "", True, False)
            if wait_seconds > 0:
                self._call_ib("sleep", wait_seconds)
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
            self._set_last_error("request_market_data", "Not connected to IBKR.")
            return None
        try:
            result = self._resolve_maybe_awaitable(
                self.ib.reqMktData(
                    contract,
                    generic_tick_list,
                    snapshot,
                    regulatory_snapshot,
                )
            )
            self.clear_last_error()
            return result
        except Exception:
            self._set_last_error("request_market_data", "reqMktData failed.")
            return None

    def cancel_market_data(self, contract) -> bool:
        if not self.is_connected() or not hasattr(self.ib, "cancelMktData"):
            return False
        try:
            self._call_ib("cancelMktData", contract)
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
            result = self._call_ib(
                "reqHistoricalData",
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                keepUpToDate=False,
            )
            return result or []
        except Exception:
            return []

    def get_head_timestamp(self, contract, what_to_show: str = "MIDPOINT", use_rth: bool = False):
        if not self.is_connected():
            return None
        try:
            return self._call_ib(
                "reqHeadTimeStamp",
                contract,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
            )
        except Exception:
            return None

    def qualify_contract(self, contract):
        if not self.is_connected():
            self._set_last_error("qualify_contract", "Not connected to IBKR.")
            return None
        try:
            contracts = self._resolve_maybe_awaitable(self.ib.qualifyContracts(contract))
            if contracts is None:
                self._set_last_error("qualify_contract", "No contract data returned.")
                return None
            result = contracts[0] if contracts else None
            if result is None:
                self._set_last_error("qualify_contract", "No qualified contract found.")
                return None
            self.clear_last_error()
            return result
        except Exception as exc:
            self._set_last_error("qualify_contract", exc)
            return None

    def get_contract_details(self, contract):
        if not self.is_connected():
            return []
        try:
            result = self._call_ib("reqContractDetails", contract)
            return result or []
        except Exception:
            return []

    def get_account_values(self):
        if not self.is_connected():
            return []
        try:
            result = self._call_ib("accountValues")
            return result or []
        except Exception:
            return []

    def get_open_orders_snapshot(self):
        if not self.is_connected():
            return []
        try:
            result = self._resolve_maybe_awaitable(self.ib.openOrders())
            return result or []
        except Exception:
            return []

    def request_all_open_orders(self):
        if not self.is_connected():
            return []
        try:
            if hasattr(self.ib, "reqAllOpenOrders"):
                self._call_ib("reqAllOpenOrders")
            if hasattr(self.ib, "openOrders"):
                result = self._call_ib("openOrders")
                return result or []
            return []
        except Exception:
            return []

    def request_completed_orders(self, api_only: bool = False):
        if not self.is_connected() or not hasattr(self.ib, "reqCompletedOrders"):
            return []
        try:
            result = self._call_ib("reqCompletedOrders", apiOnly=api_only)
            return result or []
        except TypeError:
            try:
                result = self._call_ib("reqCompletedOrders", api_only)
                return result or []
            except Exception:
                return []
        except Exception:
            return []

    def get_open_trades_snapshot(self):
        if not self.is_connected() or not hasattr(self.ib, "openTrades"):
            return []
        try:
            result = self._call_ib("openTrades")
            return result or []
        except Exception:
            return []

    def get_fills_snapshot(self):
        if not self.is_connected():
            return []
        try:
            result = self._resolve_maybe_awaitable(self.ib.fills())
            return result or []
        except Exception:
            return []

    def place_order(self, contract, order):
        if not self.is_connected() or not hasattr(self.ib, "placeOrder"):
            if not self.is_connected():
                self._set_last_error("place_order", "Not connected to IBKR.")
            else:
                self._set_last_error("place_order", "IB API missing placeOrder.")
            return None
        try:
            trade = self._resolve_maybe_awaitable(self.ib.placeOrder(contract, order))
            if trade is None:
                self._set_last_error("place_order", "API returned no trade object.")
                return None
            self.clear_last_error()
            return trade
        except Exception as exc:
            self._set_last_error("place_order", exc)
            return None

    def build_bracket_orders(
        self,
        side: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
    ):
        if not self.is_connected() or not hasattr(self.ib, "bracketOrder"):
            if not self.is_connected():
                self._set_last_error("build_bracket_orders", "Not connected to IBKR.")
            else:
                self._set_last_error("build_bracket_orders", "IB API missing bracketOrder.")
            return []
        try:
            orders = self._call_ib(
                "bracketOrder",
                side,
                quantity,
                limit_price,
                take_profit_price,
                stop_loss_price,
            )
            if not orders:
                self._set_last_error("build_bracket_orders", "Bracket creation returned no orders.")
                return []
            self.clear_last_error()
            return orders
        except Exception as exc:
            self._set_last_error("build_bracket_orders", exc)
            return []

    def replace_order(self, contract, order_with_existing_order_id):
        return self.place_order(contract, order_with_existing_order_id)

    def cancel_order(self, trade_or_order) -> bool:
        if not self.is_connected() or not hasattr(self.ib, "cancelOrder"):
            if not self.is_connected():
                self._set_last_error("cancel_order", "Not connected to IBKR.")
            else:
                self._set_last_error("cancel_order", "IB API missing cancelOrder.")
            return False
        try:
            self._call_ib("cancelOrder", trade_or_order)
            self.clear_last_error()
            return True
        except Exception as first_exc:
            order_obj = getattr(trade_or_order, "order", None)
            if order_obj is None:
                self._set_last_error("cancel_order", first_exc)
                return False
            try:
                self._call_ib("cancelOrder", order_obj)
                self.clear_last_error()
                return True
            except Exception as second_exc:
                self._set_last_error("cancel_order", second_exc)
                return False

    def what_if_order(self, contract, order):
        if not self.is_connected() or not hasattr(self.ib, "whatIfOrder"):
            if not self.is_connected():
                self._set_last_error("what_if_order", "Not connected to IBKR.")
            else:
                self._set_last_error("what_if_order", "IB API missing whatIfOrder.")
            return None
        try:
            result = self._resolve_maybe_awaitable(self.ib.whatIfOrder(contract, order))
            if result is None:
                self._set_last_error("what_if_order", "What-If returned no payload.")
                return None
            self.clear_last_error()
            return result
        except Exception as exc:
            self._set_last_error("what_if_order", exc)
            return None

    def cancel_all_open_orders(self):
        if not self.is_connected():
            message = "Not connected to IBKR."
            self._set_last_error("cancel_all_open_orders", message)
            return False, 0, message

        self.clear_last_error()
        open_orders = self.get_open_orders_snapshot()
        if not isinstance(open_orders, list):
            open_orders = []
        total_open = len(open_orders)

        if hasattr(self.ib, "reqGlobalCancel"):
            try:
                self._call_ib("reqGlobalCancel")
            except Exception as exc:
                self._set_last_error("cancel_all_open_orders", exc)

        cancelled = 0
        failed = 0
        for order in open_orders:
            if self.cancel_order(order):
                cancelled += 1
            else:
                failed += 1

        if failed > 0:
            message = f"Cancelled {cancelled}/{total_open} open orders."
            self._set_last_error("cancel_all_open_orders", message)
            return False, cancelled, message
        if total_open == 0:
            self.clear_last_error()
            return True, 0, "No open orders to cancel."

        self.clear_last_error()
        return True, cancelled, f"Cancelled {cancelled} open orders."

    def get_executions_snapshot(self):
        if not self.is_connected():
            return []
        try:
            result = self._call_ib("reqExecutions")
            return result or []
        except Exception:
            return []

    def get_pnl_snapshot(self, account: str, model_code: str = ""):
        if not self.is_connected():
            return None
        try:
            pnl = self._call_ib("reqPnL", account, modelCode=model_code)
            self._call_ib("sleep", 0)
            return pnl
        except Exception:
            return None

    def get_pnl_single_snapshot(self, account: str, con_id: int, model_code: str = ""):
        if not self.is_connected():
            return None
        try:
            pnl_single = self._call_ib("reqPnLSingle", account, modelCode=model_code, conId=con_id)
            self._call_ib("sleep", 0)
            return pnl_single
        except Exception:
            return None

    def get_market_depth_snapshot(self, contract, num_rows: int = 5, wait_seconds: float = 1.0):
        if not self.is_connected():
            return {"bids": [], "asks": []}
        try:
            ticker = self._call_ib("reqMktDepth", contract, numRows=num_rows)
            if wait_seconds > 0:
                self._call_ib("sleep", wait_seconds)
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
                    self._call_ib("cancelMktDepth", contract)
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
