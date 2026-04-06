import asyncio
import inspect
import math
import time
from typing import Any

from ib_insync import Forex, IB, LimitOrder, MarketOrder, StopOrder


class IBClient:
    # Initialize IB connection parameters and cached runtime state.
    def __init__(
        self,
        ib: IB,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        readonly: bool = False,
        ticker: Any = None,
    ) -> None:
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
    # Resolve values that may be coroutine-like in some ib_insync runtimes.
    def _resolve_maybe_awaitable(value: Any) -> Any:
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

    # Call an IB method by name and resolve async/sync return variants.
    def _call_ib(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.ib, method_name, None)
        if method is None:
            raise AttributeError(f"IB client has no method '{method_name}'")
        return self._resolve_maybe_awaitable(method(*args, **kwargs))

    @staticmethod
    # Return True when current thread already has an asyncio event loop.
    def _thread_has_event_loop() -> bool:
        try:
            asyncio.get_event_loop_policy().get_event_loop()
            return True
        except RuntimeError:
            return False

    @staticmethod
    # Ensure the current thread has an event loop for sync IB helpers that expect one.
    def _ensure_default_event_loop() -> None:
        if IBClient._thread_has_event_loop():
            return
        asyncio.set_event_loop(asyncio.new_event_loop())

    # Safely pump IB network events without creating unawaited coroutine warnings.
    def _safe_ib_sleep(self, seconds: float) -> None:
        if not hasattr(self.ib, "sleep") or not self._thread_has_event_loop():
            return
        self._resolve_maybe_awaitable(self.ib.sleep(max(0.0, float(seconds))))

    # Reset the last recorded IB error context/message.
    def clear_last_error(self) -> None:
        self._last_error_context = ""
        self._last_error_message = ""

    # Store a normalized context/message for the latest IB error.
    def _set_last_error(self, context: str, error: Any) -> None:
        self._last_error_context = str(context or "ib").strip() or "ib"
        self._last_error_message = str(error).strip()

    # Return the latest IB error payload when present.
    def get_last_error(self) -> dict[str, str] | None:
        if not self._last_error_message:
            return None
        return {
            "context": self._last_error_context or "ib",
            "message": self._last_error_message,
        }

    # Return the latest IB error as a compact display string.
    def get_last_error_text(self) -> str:
        payload = self.get_last_error()
        if payload is None:
            return ""
        return f"{payload['context']}: {payload['message']}"

    # Connect to IB and optionally trigger account summary bootstrap.
    def connect(self, timeout: float = 1.0) -> bool:
        self.clear_last_error()
        if self.is_connected():
            return True
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

    # Subscribe to live market data for a ticker symbol.
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

    # Detach listeners and stop current live market-data stream.
    def stop_live_streaming(self) -> None:
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

    # Connect to IB and prepare a ticker stream when requested.
    def connect_and_prepare(self, ticker: str = "", timeout: float = 1.0) -> Any:
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

    # Return True when IB transport reports an active connection.
    def is_connected(self) -> bool:
        return bool(self.ib.isConnected())

    # Return user-facing connection state text.
    def get_connection_state(self, connecting: bool = False) -> str:
        if self.is_connected():
            return "connected"
        if connecting:
            return "connecting"
        return "disconnected"

    # Process pending IB events and drain buffered tick updates.
    def process_messages(self) -> list[dict[str, Any]]:
        try:
            self._safe_ib_sleep(0)
        except Exception:
            pass
        return self.drain_received_ticks()

    # Pump IB network queue without draining received tick buffer.
    def pump_network(self) -> None:
        try:
            self._safe_ib_sleep(0)
        except Exception:
            pass

    # Attach local tick callback to the active ticker update event.
    def _attach_ticker_listener(self) -> None:
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

    # Remove local tick callback from the previous ticker source.
    def _detach_ticker_listener(self) -> None:
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
    # Normalize tick time payloads to display-friendly text.
    def _format_tick_time(raw_time: Any) -> str:
        if raw_time is None:
            return "--"
        if hasattr(raw_time, "strftime"):
            try:
                return raw_time.strftime("%H:%M:%S.%f")[:-3]
            except Exception:
                return str(raw_time)
        return str(raw_time)

    # Push incoming ticker updates into a drained tick buffer.
    def _on_ticker_update(self, ticker: Any = None, *_: Any) -> None:
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

    # Return buffered ticks and clear the internal tick queue.
    def drain_received_ticks(self) -> list[dict[str, Any]]:
        ticks = self._received_ticks
        self._received_ticks = []
        return ticks

    @staticmethod
    # Return True when the price value is usable.
    def _is_valid_price(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return not math.isnan(value)
        return True

    # Return latest valid bid/ask pair, reusing cached values when needed.
    def get_latest_bid_ask(self) -> tuple[Any, Any]:
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

    # Return account summary and positions snapshots.
    def get_portfolio_snapshot(self) -> tuple[list[Any], list[Any]]:
        if not self.is_connected():
            return [], []

        summary: list[Any] = []
        if hasattr(self.ib, "accountValues"):
            try:
                summary = self._resolve_maybe_awaitable(self.ib.accountValues()) or []
            except Exception:
                summary = []
        if not summary and hasattr(self.ib, "accountSummaryAsync"):
            try:
                summary = self._resolve_maybe_awaitable(self.ib.accountSummaryAsync()) or []
            except Exception:
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

    # Cancel account summary streaming if API support exists.
    def cancel_account_summary(self) -> None:
        if hasattr(self.ib, "cancelAccountSummary"):
            try:
                self._call_ib("cancelAccountSummary")
            except Exception:
                pass

    # Request account summary with compatibility fallback signatures.
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

    # Return read-only/read-write mode inferred from IB client object.
    def get_connection_mode(self) -> str:
        client = getattr(self.ib, "client", None)
        readonly = getattr(client, "readonly", None) if client is not None else None
        if readonly is None:
            return "unknown"
        return "read-only" if readonly else "read-write"

    # Map configured port to paper/live environment labels.
    def get_environment(self) -> str:
        if self.port in (4002, 7497):
            return "paper"
        if self.port in (4001, 7496):
            return "live"
        return "unknown"

    # Return first managed account identifier.
    def get_account(self) -> str:
        accounts = []
        if hasattr(self.ib, "managedAccounts"):
            try:
                accounts = self._resolve_maybe_awaitable(self.ib.managedAccounts())
            except Exception:
                accounts = []
        return accounts[0] if accounts else "--"

    # Return whether reqCurrentTime is supported by IB client object.
    def supports_server_time(self) -> bool:
        return hasattr(self.ib, "reqCurrentTime")

    # Return server time text and measured request latency.
    def get_server_time_and_latency(self) -> tuple[str, str]:
        if not self.supports_server_time():
            return "--", "--"
        try:
            start = time.time()
            if hasattr(self.ib, "reqCurrentTimeAsync"):
                server_time = self._resolve_maybe_awaitable(self.ib.reqCurrentTimeAsync())
            else:
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

    # Request one-shot market snapshot fields for a contract.
    def get_market_snapshot(self, contract: Any, wait_seconds: float = 1.0) -> dict[str, Any]:
        if not self.is_connected():
            return {}
        try:
            ticker = self.request_market_data(contract, snapshot=True, regulatory_snapshot=False)
            if ticker is None:
                return {}
            if wait_seconds > 0:
                self._safe_ib_sleep(wait_seconds)
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

    # Request market-data stream or snapshot for a contract.
    def request_market_data(
        self,
        contract: Any,
        generic_tick_list: str = "",
        snapshot: bool = False,
        regulatory_snapshot: bool = False,
    ) -> Any:
        if not self.is_connected():
            self._set_last_error("request_market_data", "Not connected to IBKR.")
            return None
        gateway_errors: list[tuple[int, int, str]] = []

        def _on_error(*args: Any) -> None:
            req_id = args[0] if len(args) >= 1 else 0
            error_code = args[1] if len(args) >= 2 else 0
            error_msg = args[2] if len(args) >= 3 else "Unknown gateway error."
            try:
                gateway_errors.append((int(req_id), int(error_code), str(error_msg)))
            except Exception:
                gateway_errors.append((0, 0, str(error_msg)))

        error_event_registered = False
        error_event = getattr(self.ib, "errorEvent", None)
        if error_event is not None:
            try:
                error_event += _on_error
                error_event_registered = True
            except Exception:
                error_event_registered = False
        try:
            result = self._resolve_maybe_awaitable(
                self.ib.reqMktData(
                    contract,
                    generic_tick_list,
                    snapshot,
                    regulatory_snapshot,
                )
            )
            self._safe_ib_sleep(0.15)
            competing_live_session = any(code == 10197 for _req_id, code, _msg in gateway_errors)
            if competing_live_session and hasattr(self.ib, "reqMarketDataType"):
                try:
                    self._call_ib("reqMarketDataType", 3)
                    if hasattr(self.ib, "cancelMktData"):
                        try:
                            self._call_ib("cancelMktData", contract)
                        except Exception:
                            pass
                    result = self._resolve_maybe_awaitable(
                        self.ib.reqMktData(
                            contract,
                            generic_tick_list,
                            snapshot,
                            regulatory_snapshot,
                        )
                    )
                    self._safe_ib_sleep(0.15)
                except Exception as fallback_exc:
                    if gateway_errors:
                        compact_errors = ", ".join(f"{code}:{msg}" for _req_id, code, msg in gateway_errors[-3:])
                        self._set_last_error("request_market_data", f"{fallback_exc} | Gateway errors: {compact_errors}")
                    else:
                        self._set_last_error("request_market_data", fallback_exc)
                    return None
            if result is None:
                detail = "reqMktData failed."
                if gateway_errors:
                    compact_errors = ", ".join(f"{code}:{msg}" for _req_id, code, msg in gateway_errors[-3:])
                    detail = f"{detail} Gateway errors: {compact_errors}"
                self._set_last_error("request_market_data", detail)
                return None
            self.clear_last_error()
            return result
        except Exception as exc:
            if gateway_errors:
                compact_errors = ", ".join(f"{code}:{msg}" for _req_id, code, msg in gateway_errors[-3:])
                self._set_last_error("request_market_data", f"{exc} | Gateway errors: {compact_errors}")
            else:
                self._set_last_error("request_market_data", exc)
            return None
        finally:
            if error_event_registered and error_event is not None:
                try:
                    error_event -= _on_error
                except Exception:
                    pass

    # Cancel market-data subscription for a contract.
    def cancel_market_data(self, contract: Any) -> bool:
        if not self.is_connected() or not hasattr(self.ib, "cancelMktData"):
            return False
        try:
            self._call_ib("cancelMktData", contract)
            return True
        except Exception:
            return False

    # Request historical bars for a contract and time range.
    def get_historical_bars(
        self,
        contract: Any,
        duration: str = "1 D",
        bar_size: str = "1 min",
        what_to_show: str = "MIDPOINT",
        use_rth: bool = False,
    ) -> list[Any]:
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

    # Request earliest historical timestamp available for a contract.
    def get_head_timestamp(self, contract: Any, what_to_show: str = "MIDPOINT", use_rth: bool = False) -> Any:
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

    # Qualify a contract through IB and return first qualified result.
    def qualify_contract(self, contract: Any) -> Any:
        if not self.is_connected():
            self._set_last_error("qualify_contract", "Not connected to IBKR.")
            return None
        try:
            self._ensure_default_event_loop()
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

    # Return contract details list for a contract request.
    def get_contract_details(self, contract: Any) -> list[Any]:
        if not self.is_connected():
            return []
        try:
            result = self._call_ib("reqContractDetails", contract)
            return result or []
        except Exception:
            return []

    # Return account values snapshot.
    def get_account_values(self) -> list[Any]:
        if not self.is_connected():
            return []
        try:
            result = self._call_ib("accountValues")
            return result or []
        except Exception:
            return []

    # Return currently open orders snapshot.
    def get_open_orders_snapshot(self) -> list[Any]:
        if not self.is_connected():
            return []
        try:
            result = self._resolve_maybe_awaitable(self.ib.openOrders())
            return result or []
        except Exception:
            return []

    # Request all open orders and return latest available snapshot.
    def request_all_open_orders(self) -> list[Any]:
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

    # Request completed orders with compatibility fallback signatures.
    def request_completed_orders(self, api_only: bool = False) -> list[Any]:
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

    # Return open trades snapshot.
    def get_open_trades_snapshot(self) -> list[Any]:
        if not self.is_connected() or not hasattr(self.ib, "openTrades"):
            return []
        try:
            result = self._call_ib("openTrades")
            return result or []
        except Exception:
            return []

    # Return recent fills snapshot.
    def get_fills_snapshot(self) -> list[Any]:
        if not self.is_connected():
            return []
        try:
            result = self._resolve_maybe_awaitable(self.ib.fills())
            return result or []
        except Exception:
            return []

    # Place an order and return the created trade object.
    def place_order(self, contract: Any, order: Any) -> Any:
        if not self.is_connected() or not hasattr(self.ib, "placeOrder"):
            if not self.is_connected():
                self._set_last_error("place_order", "Not connected to IBKR.")
            else:
                self._set_last_error("place_order", "IB API missing placeOrder.")
            return None

        gateway_errors: list[tuple[int, int, str]] = []

        def _on_error(*args: Any) -> None:
            req_id = args[0] if len(args) >= 1 else 0
            error_code = args[1] if len(args) >= 2 else 0
            error_msg = args[2] if len(args) >= 3 else "Unknown gateway error."
            try:
                gateway_errors.append((int(req_id), int(error_code), str(error_msg)))
            except Exception:
                gateway_errors.append((0, 0, str(error_msg)))

        error_event_registered = False
        error_event = getattr(self.ib, "errorEvent", None)
        if error_event is not None:
            try:
                error_event += _on_error
                error_event_registered = True
            except Exception:
                error_event_registered = False
        try:
            # placeOrder may require an event loop when called from worker threads.
            self._ensure_default_event_loop()
            trade = self._resolve_maybe_awaitable(self.ib.placeOrder(contract, order))
            if trade is None:
                detail = "API returned no trade object."
                if gateway_errors:
                    compact_errors = ", ".join(f"{code}:{msg}" for _req_id, code, msg in gateway_errors[-3:])
                    detail = f"{detail} Gateway errors: {compact_errors}"
                self._set_last_error("place_order", detail)
                return None
            self.clear_last_error()
            return trade
        except Exception as exc:
            if gateway_errors:
                compact_errors = ", ".join(f"{code}:{msg}" for _req_id, code, msg in gateway_errors[-3:])
                self._set_last_error("place_order", f"{exc} | Gateway errors: {compact_errors}")
            else:
                self._set_last_error("place_order", exc)
            return None
        finally:
            if error_event_registered and error_event is not None:
                try:
                    error_event -= _on_error
                except Exception:
                    pass

    # Build IB bracket orders for parent, take-profit, and stop-loss legs.
    def build_bracket_orders(
        self,
        side: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        parent_order_type: str = "LMT",
    ) -> list[Any]:
        if not self.is_connected():
            if not self.is_connected():
                self._set_last_error("build_bracket_orders", "Not connected to IBKR.")
            return []
        normalized_side = str(side).strip().upper()
        if normalized_side not in {"BUY", "SELL"}:
            self._set_last_error("build_bracket_orders", "Invalid bracket side.")
            return []
        normalized_parent_type = str(parent_order_type).strip().upper()
        if normalized_parent_type not in {"LMT", "MKT"}:
            self._set_last_error("build_bracket_orders", "Invalid parent order type for bracket.")
            return []

        if normalized_parent_type == "MKT":
            client = getattr(self.ib, "client", None)
            get_req_id = getattr(client, "getReqId", None)
            if get_req_id is None:
                self._set_last_error("build_bracket_orders", "IB API missing client.getReqId for MKT bracket.")
                return []
            try:
                reverse_side = "BUY" if normalized_side == "SELL" else "SELL"
                parent_order_id = int(get_req_id())
                parent = MarketOrder(
                    normalized_side,
                    quantity,
                    orderId=parent_order_id,
                    transmit=False,
                )
                take_profit = LimitOrder(
                    reverse_side,
                    quantity,
                    take_profit_price,
                    orderId=int(get_req_id()),
                    transmit=False,
                    parentId=parent_order_id,
                )
                stop_loss = StopOrder(
                    reverse_side,
                    quantity,
                    stop_loss_price,
                    orderId=int(get_req_id()),
                    transmit=True,
                    parentId=parent_order_id,
                )
                self.clear_last_error()
                return [parent, take_profit, stop_loss]
            except Exception as exc:
                self._set_last_error("build_bracket_orders", exc)
                return []

        if not hasattr(self.ib, "bracketOrder"):
            self._set_last_error("build_bracket_orders", "IB API missing bracketOrder.")
            return []
        try:
            orders = self._call_ib(
                "bracketOrder",
                normalized_side,
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

    # Replace an existing order via placeOrder with an explicit order id.
    def replace_order(self, contract: Any, order_with_existing_order_id: Any) -> Any:
        return self.place_order(contract, order_with_existing_order_id)

    # Cancel an order/trade object with fallback to nested order.
    def cancel_order(self, trade_or_order: Any) -> bool:
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

    # Run IB What-If for an order and return margin/commission payload.
    def what_if_order(self, contract: Any, order: Any) -> Any:
        if not self.is_connected() or not hasattr(self.ib, "whatIfOrder"):
            if not self.is_connected():
                self._set_last_error("what_if_order", "Not connected to IBKR.")
            else:
                self._set_last_error("what_if_order", "IB API missing whatIfOrder.")
            return None

        gateway_errors: list[tuple[int, int, str]] = []

        def _on_error(*args: Any) -> None:
            req_id = args[0] if len(args) >= 1 else 0
            error_code = args[1] if len(args) >= 2 else 0
            error_msg = args[2] if len(args) >= 3 else "Unknown gateway error."
            try:
                gateway_errors.append((int(req_id), int(error_code), str(error_msg)))
            except Exception:
                gateway_errors.append((0, 0, str(error_msg)))

        error_event_registered = False
        error_event = getattr(self.ib, "errorEvent", None)
        if error_event is not None:
            try:
                error_event += _on_error
                error_event_registered = True
            except Exception:
                error_event_registered = False
        try:
            self._ensure_default_event_loop()
            try:
                result = self._resolve_maybe_awaitable(self.ib.whatIfOrder(contract, order))
            except RuntimeError as exc:
                message = str(exc).lower()
                if "no current event loop" not in message:
                    raise
                asyncio.set_event_loop(asyncio.new_event_loop())
                result = self._resolve_maybe_awaitable(self.ib.whatIfOrder(contract, order))
            if result is None:
                detail = "What-If returned no payload."
                if gateway_errors:
                    compact_errors = ", ".join(f"{code}:{msg}" for _req_id, code, msg in gateway_errors[-3:])
                    detail = f"{detail} Gateway errors: {compact_errors}"
                self._set_last_error("what_if_order", detail)
                return None
            self.clear_last_error()
            return result
        except Exception as exc:
            if gateway_errors:
                compact_errors = ", ".join(f"{code}:{msg}" for _req_id, code, msg in gateway_errors[-3:])
                self._set_last_error("what_if_order", f"{exc} | Gateway errors: {compact_errors}")
            else:
                self._set_last_error("what_if_order", exc)
            return None
        finally:
            if error_event_registered and error_event is not None:
                try:
                    error_event -= _on_error
                except Exception:
                    pass

    # Cancel all currently open orders and return summary tuple.
    def cancel_all_open_orders(self) -> tuple[bool, int, str]:
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

    # Return executions snapshot.
    def get_executions_snapshot(self) -> list[Any]:
        if not self.is_connected():
            return []
        try:
            result = self._call_ib("reqExecutions")
            return result or []
        except Exception:
            return []

    # Request account-level PnL snapshot object.
    def get_pnl_snapshot(self, account: str, model_code: str = "") -> Any:
        if not self.is_connected():
            return None
        try:
            pnl = self._call_ib("reqPnL", account, modelCode=model_code)
            self._safe_ib_sleep(0)
            return pnl
        except Exception:
            return None

    # Request contract-level PnL snapshot object.
    def get_pnl_single_snapshot(self, account: str, con_id: int, model_code: str = "") -> Any:
        if not self.is_connected():
            return None
        try:
            pnl_single = self._call_ib("reqPnLSingle", account, modelCode=model_code, conId=con_id)
            self._safe_ib_sleep(0)
            return pnl_single
        except Exception:
            return None

    # Request level-2 market depth snapshot for a contract.
    def get_market_depth_snapshot(
        self,
        contract: Any,
        num_rows: int = 5,
        wait_seconds: float = 1.0,
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.is_connected():
            return {"bids": [], "asks": []}
        try:
            ticker = self._call_ib("reqMktDepth", contract, numRows=num_rows)
            if wait_seconds > 0:
                self._safe_ib_sleep(wait_seconds)
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

    # Return compact connection status payload for UI polling.
    def get_status_snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.is_connected(),
            "mode": self.get_connection_mode(),
            "env": self.get_environment(),
            "client_id": str(self.client_id),
            "account": self.get_account(),
        }
