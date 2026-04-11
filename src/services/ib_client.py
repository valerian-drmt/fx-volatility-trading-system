import logging
import math
import time
from typing import Any

from ib_insync import IB, Contract, Forex, LimitOrder, MarketOrder, StopOrder


class IBClient:
    def __init__(
        self,
        ib: IB,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        readonly: bool = False,
        ticker: Any = None,
    ) -> None:
        """Initialize IB connection parameters and cached runtime state.

        Args:
            ib: The ib_insync IB instance to wrap.
            host: IB Gateway/TWS hostname.
            port: IB Gateway/TWS port.
            client_id: Unique client identifier for this connection.
            readonly: If True, disallow order placement.
            ticker: Optional pre-existing ticker to attach.
        """
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
    def _snapshot_identity(item: Any) -> tuple[Any, ...]:
        """Build a stable identity tuple for snapshot objects returned by IB."""
        if isinstance(item, dict):
            return (
                item.get("execId"),
                item.get("orderId"),
                item.get("time"),
                item.get("symbol"),
                item.get("localSymbol"),
                item.get("side"),
                item.get("shares"),
                item.get("qty"),
                item.get("quantity"),
                item.get("price"),
                item.get("avgPrice"),
                item.get("avg_price"),
            )

        execution = getattr(item, "execution", None)
        payload = execution if execution is not None else item
        contract = getattr(item, "contract", None)
        return (
            getattr(payload, "execId", None),
            getattr(payload, "orderId", None),
            getattr(payload, "time", None),
            getattr(contract, "localSymbol", None),
            getattr(contract, "symbol", None),
            getattr(payload, "side", None),
            getattr(payload, "shares", None),
            getattr(payload, "qty", None),
            getattr(payload, "quantity", None),
            getattr(payload, "price", None),
            getattr(payload, "avgPrice", None),
            getattr(payload, "avg_price", None),
        )

    def clear_last_error(self) -> None:
        """Reset the last recorded IB error context/message."""
        self._last_error_context = ""
        self._last_error_message = ""

    def _set_last_error(self, context: str, error: Any) -> None:
        """Store a normalized context/message for the latest IB error."""
        self._last_error_context = str(context or "ib").strip() or "ib"
        self._last_error_message = str(error).strip()

    def get_last_error(self) -> dict[str, str] | None:
        """Return the latest IB error payload when present."""
        if not self._last_error_message:
            return None
        return {
            "context": self._last_error_context or "ib",
            "message": self._last_error_message,
        }

    def get_last_error_text(self) -> str:
        """Return the latest IB error as a compact display string."""
        payload = self.get_last_error()
        if payload is None:
            return ""
        return f"{payload['context']}: {payload['message']}"

    def connect(self, timeout: float = 1.0) -> bool:
        """Connect to IB and optionally trigger account summary bootstrap.

        Args:
            timeout: Connection timeout in seconds.

        Returns:
            True if connected successfully.
        """
        self.clear_last_error()
        if self.is_connected():
            return True
        try:
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
        except Exception as exc:
            self._set_last_error("connect", exc)
            return False
        if hasattr(self.ib, "reqAccountSummary"):
            try:
                self.ib.reqAccountSummary()
            except Exception:
                pass
        connected = self.is_connected()
        if connected:
            self.clear_last_error()
            return True
        self._set_last_error("connect", f"Connection to {self.host}:{self.port} failed.")
        return False

    def start_live_streaming(self, ticker: str) -> bool:
        """Subscribe to live market data for a ticker symbol.

        Args:
            ticker: The symbol to stream (e.g. ``"EURUSD"``).

        Returns:
            True if streaming was started successfully.
        """
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

    def _resolve_front_future(self) -> Any:
        """Resolve the front quarterly EUR future (6E) on CME."""
        from datetime import date, timedelta

        fut = Contract()
        fut.symbol = "EUR"
        fut.secType = "FUT"
        fut.exchange = "CME"
        fut.currency = "USD"

        try:
            details = self.ib.reqContractDetails(fut)
        except Exception:
            return None
        if not details:
            return None

        today = date.today()
        min_exp = (today + timedelta(days=7)).strftime("%Y%m%d")
        quarterly = [
            d for d in details
            if d.contract.lastTradeDateOrContractMonth >= min_exp
            and int(d.contract.lastTradeDateOrContractMonth[4:6]) in {3, 6, 9, 12}
        ]
        if not quarterly:
            return None
        quarterly.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)
        resolved = quarterly[0].contract
        logging.getLogger("ib_client").info(
            "Streaming future: %s exp=%s", resolved.localSymbol, resolved.lastTradeDateOrContractMonth)
        return resolved

    def stop_live_streaming(self) -> None:
        """Detach listeners and stop current live market-data stream."""
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

    def connect_and_prepare(self, ticker: str = "", timeout: float = 1.0) -> Any:
        """Connect to IB and prepare a ticker stream when requested.

        Args:
            ticker: Symbol to stream after connecting. Falls back to front EUR future.
            timeout: Connection timeout in seconds.

        Returns:
            The active ticker object, or None on failure.
        """
        self.connect(timeout=timeout)

        if self.ticker is not None:
            return self.ticker

        symbol = str(ticker).strip().upper()
        if symbol:
            self.start_live_streaming(symbol)
            return self.ticker

        # Default stream: front EUR future
        contract = self._resolve_front_future() or Forex("EURUSD")
        requested_ticker = self.request_market_data(contract, snapshot=False, regulatory_snapshot=False)
        if requested_ticker is not None:
            self.ticker = requested_ticker
            self._attach_ticker_listener()
        return self.ticker

    def is_connected(self) -> bool:
        """Return True when IB transport reports an active connection."""
        return bool(self.ib.isConnected())

    def get_connection_state(self, connecting: bool = False) -> str:
        """Return user-facing connection state text.

        Args:
            connecting: If True and not yet connected, return ``"connecting"``.
        """
        if self.is_connected():
            return "connected"
        if connecting:
            return "connecting"
        return "disconnected"

    def process_messages(self) -> list[dict[str, Any]]:
        """Process pending IB events and drain buffered tick updates."""
        try:
            self.ib.sleep(0)
        except Exception:
            pass
        return self.drain_received_ticks()

    def pump_network(self) -> None:
        """Pump IB network queue without draining received tick buffer."""
        try:
            self.ib.sleep(0)
        except Exception:
            pass

    def _attach_ticker_listener(self) -> None:
        """Attach local tick callback to the active ticker update event."""
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

    def _detach_ticker_listener(self) -> None:
        """Remove local tick callback from the previous ticker source."""
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
    def _format_tick_time(raw_time: Any) -> str:
        """Normalize tick time payloads to display-friendly text."""
        if raw_time is None:
            return "--"
        if hasattr(raw_time, "strftime"):
            try:
                return raw_time.strftime("%H:%M:%S.%f")[:-3]
            except Exception:
                return str(raw_time)
        return str(raw_time)

    def _on_ticker_update(self, ticker: Any = None, *_: Any) -> None:
        """Push incoming ticker updates into a drained tick buffer."""
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

    def drain_received_ticks(self) -> list[dict[str, Any]]:
        """Return buffered ticks and clear the internal tick queue."""
        ticks = self._received_ticks
        self._received_ticks = []
        return ticks

    @staticmethod
    def _is_valid_price(value: Any) -> bool:
        """Return True when the price value is usable (not None, not NaN)."""
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return not math.isnan(value)
        return True

    def get_latest_bid_ask(self) -> tuple[Any, Any]:
        """Return latest valid bid/ask pair, reusing cached values when needed."""
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

    def get_portfolio_snapshot(self) -> tuple[list[Any], list[Any]]:
        """Return account summary and positions snapshots."""
        if not self.is_connected():
            return [], []

        summary: list[Any] = []
        if hasattr(self.ib, "accountValues"):
            try:
                summary = self.ib.accountValues() or []
            except Exception:
                summary = []
        if not summary and hasattr(self.ib, "accountSummaryAsync"):
            try:
                summary = self.ib.accountSummary() or []
            except Exception:
                summary = []
        positions = []
        if hasattr(self.ib, "positions"):
            try:
                positions = self.ib.positions()
                if positions is None:
                    positions = []
            except Exception:
                positions = []
        return summary, positions

    def cancel_account_summary(self) -> None:
        """Cancel account summary streaming if API support exists."""
        if hasattr(self.ib, "cancelAccountSummary"):
            try:
                self.ib.cancelAccountSummary()
            except Exception:
                pass

    def request_account_summary(self, group_name: str = "All", tags: str = "NetLiquidation,AvailableFunds") -> bool:
        """Request account summary with compatibility fallback signatures.

        Args:
            group_name: IB account group name.
            tags: Comma-separated summary tags to request.

        Returns:
            True if the request was accepted.
        """
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
        """Return read-only/read-write mode inferred from IB client object."""
        client = getattr(self.ib, "client", None)
        readonly = getattr(client, "readonly", None) if client is not None else None
        if readonly is None:
            return "unknown"
        return "read-only" if readonly else "read-write"

    def get_environment(self) -> str:
        """Map configured port to paper/live environment labels."""
        if self.port in (4002, 7497):
            return "paper"
        if self.port in (4001, 7496):
            return "live"
        return "unknown"

    def get_account(self) -> str:
        """Return first managed account identifier."""
        accounts = []
        if hasattr(self.ib, "managedAccounts"):
            try:
                accounts = self.ib.managedAccounts()
            except Exception:
                accounts = []
        return accounts[0] if accounts else "--"

    def supports_server_time(self) -> bool:
        """Return whether reqCurrentTime is supported by IB client object."""
        return hasattr(self.ib, "reqCurrentTime")

    def get_server_time_and_latency(self) -> tuple[str, str]:
        """Return server time text and measured request latency."""
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

    def get_market_snapshot(self, contract: Any, wait_seconds: float = 1.0) -> dict[str, Any]:
        """Request one-shot market snapshot fields for a contract.

        Args:
            contract: IB contract to snapshot.
            wait_seconds: Time to wait for data to arrive.

        Returns:
            Dict with bid, ask, last, close, volume, time fields.
        """
        if not self.is_connected():
            return {}
        try:
            ticker = self.request_market_data(contract, snapshot=True, regulatory_snapshot=False)
            if ticker is None:
                return {}
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
        contract: Any,
        generic_tick_list: str = "",
        snapshot: bool = False,
        regulatory_snapshot: bool = False,
    ) -> Any:
        """Request market-data stream or snapshot for a contract.

        Args:
            contract: IB contract to subscribe to.
            generic_tick_list: Comma-separated generic tick type codes.
            snapshot: If True, request a one-shot snapshot instead of streaming.
            regulatory_snapshot: If True, request a regulatory snapshot.

        Returns:
            The ticker object, or None on failure.
        """
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
            result = self.ib.reqMktData(
                contract,
                generic_tick_list,
                snapshot,
                regulatory_snapshot,
            )
            self.ib.sleep(0.15)
            competing_live_session = any(code == 10197 for _req_id, code, _msg in gateway_errors)
            if competing_live_session and hasattr(self.ib, "reqMarketDataType"):
                try:
                    self.ib.reqMarketDataType(3)
                    if hasattr(self.ib, "cancelMktData"):
                        try:
                            self.ib.cancelMktData(contract)
                        except Exception:
                            pass
                    result = self.ib.reqMktData(
                        contract,
                        generic_tick_list,
                        snapshot,
                        regulatory_snapshot,
                    )
                    self.ib.sleep(0.15)
                    # Restore live type so FOP and other subscriptions are not affected
                    self.ib.reqMarketDataType(1)
                except Exception as fallback_exc:
                    self.ib.reqMarketDataType(1)  # always restore
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

    def cancel_market_data(self, contract: Any) -> bool:
        """Cancel market-data subscription for a contract."""
        if not self.is_connected() or not hasattr(self.ib, "cancelMktData"):
            return False
        try:
            self.ib.cancelMktData(contract)
            return True
        except Exception:
            return False

    def get_historical_bars(
        self,
        contract: Any,
        duration: str = "1 D",
        bar_size: str = "1 min",
        what_to_show: str = "MIDPOINT",
        use_rth: bool = False,
    ) -> list[Any]:
        """Request historical bars for a contract and time range.

        Args:
            contract: IB contract to query.
            duration: Duration string (e.g. ``"1 D"``, ``"1 W"``).
            bar_size: Bar size setting (e.g. ``"1 min"``, ``"1 hour"``).
            what_to_show: Data type (``"MIDPOINT"``, ``"TRADES"``, etc.).
            use_rth: If True, restrict to regular trading hours.

        Returns:
            List of bar objects, or empty list on failure.
        """
        if not self.is_connected():
            return []
        try:
            result = self.ib.reqHistoricalData(
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

    def get_head_timestamp(self, contract: Any, what_to_show: str = "MIDPOINT", use_rth: bool = False) -> Any:
        """Request earliest historical timestamp available for a contract.

        Args:
            contract: IB contract to query.
            what_to_show: Data type to check.
            use_rth: If True, restrict to regular trading hours.

        Returns:
            Timestamp object, or None on failure.
        """
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

    def qualify_contract(self, contract: Any) -> Any:
        """Qualify a contract through IB and return first qualified result."""
        if not self.is_connected():
            self._set_last_error("qualify_contract", "Not connected to IBKR.")
            return None
        try:
            contracts = self.ib.qualifyContracts(contract)
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

    def get_contract_details(self, contract: Any) -> list[Any]:
        """Return contract details list for a contract request."""
        if not self.is_connected():
            return []
        try:
            result = self.ib.reqContractDetails(contract)
            return result or []
        except Exception:
            return []

    def get_account_values(self) -> list[Any]:
        """Return account values snapshot."""
        if not self.is_connected():
            return []
        try:
            result = self.ib.accountValues()
            return result or []
        except Exception:
            return []

    _INACTIVE_STATUSES = {"cancelled", "inactive", "apicancelled", "filled", "pendingcancel"}

    def get_open_orders_snapshot(self) -> list[Any]:
        """Return currently open trades snapshot, excluding cancelled/filled."""
        if not self.is_connected():
            return []
        try:
            if hasattr(self.ib, "openTrades"):
                trades = self.ib.openTrades() or []
                return [
                    t for t in trades
                    if str(getattr(getattr(t, "orderStatus", None), "status", "")).lower()
                    not in self._INACTIVE_STATUSES
                ]
            result = self.ib.openOrders()
            return result or []
        except Exception:
            return []

    def request_all_open_orders(self) -> list[Any]:
        """Request all open orders and return latest available snapshot."""
        if not self.is_connected():
            return []
        try:
            if hasattr(self.ib, "reqAllOpenOrders"):
                self.ib.reqAllOpenOrders()
            if hasattr(self.ib, "openOrders"):
                result = self.ib.openOrders()
                return result or []
            return []
        except Exception:
            return []

    def request_completed_orders(self, api_only: bool = False) -> list[Any]:
        """Request completed orders with compatibility fallback signatures.

        Args:
            api_only: If True, return only API-placed orders.
        """
        if not self.is_connected() or not hasattr(self.ib, "reqCompletedOrders"):
            return []
        try:
            result = self.ib.reqCompletedOrders(apiOnly=api_only)
            return result or []
        except TypeError:
            try:
                result = self.ib.reqCompletedOrders(api_only)
                return result or []
            except Exception:
                return []
        except Exception:
            return []

    def get_open_trades_snapshot(self) -> list[Any]:
        """Return open trades snapshot."""
        if not self.is_connected() or not hasattr(self.ib, "openTrades"):
            return []
        try:
            result = self.ib.openTrades()
            return result or []
        except Exception:
            return []

    def get_fills_snapshot(self) -> list[Any]:
        """Return recent fills snapshot."""
        if not self.is_connected():
            return []
        try:
            result = self.ib.fills()
            return result or []
        except Exception:
            return []

    def get_recent_fills_snapshot(self) -> list[Any]:
        """Return recent fills merged from requested executions and local session cache."""
        if not self.is_connected():
            return []

        merged: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        for source in (self.get_executions_snapshot(), self.get_fills_snapshot()):
            if not isinstance(source, list):
                continue
            for item in source:
                identity = self._snapshot_identity(item)
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(item)
        return merged

    def place_order(self, contract: Any, order: Any) -> Any:
        """Place an order and return the created trade object.

        Args:
            contract: IB contract to trade.
            order: IB order object (LimitOrder, MarketOrder, etc.).

        Returns:
            The trade object, or None on failure.
        """
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
            trade = self.ib.placeOrder(contract, order)
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

    def build_bracket_orders(
        self,
        side: str,
        quantity: int,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        parent_order_type: str = "LMT",
    ) -> list[Any]:
        """Build IB bracket orders for parent, take-profit, and stop-loss legs.

        Args:
            side: ``"BUY"`` or ``"SELL"``.
            quantity: Number of units.
            limit_price: Limit price for the parent order.
            take_profit_price: Take-profit limit price.
            stop_loss_price: Stop-loss trigger price.
            parent_order_type: ``"LMT"`` or ``"MKT"`` for the parent leg.

        Returns:
            List of [parent, take_profit, stop_loss] order objects, or empty on failure.
        """
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
            orders = self.ib.bracketOrder(
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

    def replace_order(self, contract: Any, order_with_existing_order_id: Any) -> Any:
        """Replace an existing order via placeOrder with an explicit order id."""
        return self.place_order(contract, order_with_existing_order_id)

    def cancel_order(self, trade_or_order: Any) -> bool:
        """Cancel an order/trade object with fallback to nested order."""
        if not self.is_connected() or not hasattr(self.ib, "cancelOrder"):
            if not self.is_connected():
                self._set_last_error("cancel_order", "Not connected to IBKR.")
            else:
                self._set_last_error("cancel_order", "IB API missing cancelOrder.")
            return False
        try:
            self.ib.cancelOrder(trade_or_order)
            self.clear_last_error()
            return True
        except Exception as first_exc:
            order_obj = getattr(trade_or_order, "order", None)
            if order_obj is None:
                self._set_last_error("cancel_order", first_exc)
                return False
            try:
                self.ib.cancelOrder(order_obj)
                self.clear_last_error()
                return True
            except Exception as second_exc:
                self._set_last_error("cancel_order", second_exc)
                return False

    def what_if_order(self, contract: Any, order: Any) -> Any:
        """Run IB What-If for an order and return margin/commission payload.

        Args:
            contract: IB contract for the simulated order.
            order: IB order object to simulate.

        Returns:
            What-If result payload, or None on failure.
        """
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
            result = self.ib.whatIfOrder(contract, order)
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

    def cancel_all_open_orders(self) -> tuple[bool, int, str]:
        """Cancel all currently open orders and return summary tuple.

        Returns:
            Tuple of (success, cancelled_count, message).
        """
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
                self.ib.reqGlobalCancel()
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

    def get_executions_snapshot(self) -> list[Any]:
        """Return executions snapshot."""
        if not self.is_connected():
            return []
        try:
            result = self.ib.reqExecutions()
            return result or []
        except Exception:
            return []

    def get_pnl_snapshot(self, account: str, model_code: str = "") -> Any:
        """Request account-level PnL snapshot object.

        Args:
            account: IB account identifier.
            model_code: Optional model code filter.
        """
        if not self.is_connected():
            return None
        try:
            pnl = self.ib.reqPnL(account, modelCode=model_code)
            self.ib.sleep(0)
            return pnl
        except Exception:
            return None

    def get_pnl_single_snapshot(self, account: str, con_id: int, model_code: str = "") -> Any:
        """Request contract-level PnL snapshot object.

        Args:
            account: IB account identifier.
            con_id: Contract ID.
            model_code: Optional model code filter.
        """
        if not self.is_connected():
            return None
        try:
            pnl_single = self.ib.reqPnLSingle(account, modelCode=model_code, conId=con_id)
            self.ib.sleep(0)
            return pnl_single
        except Exception:
            return None

    def get_market_depth_snapshot(
        self,
        contract: Any,
        num_rows: int = 5,
        wait_seconds: float = 1.0,
    ) -> dict[str, list[dict[str, Any]]]:
        """Request level-2 market depth snapshot for a contract.

        Args:
            contract: IB contract to query.
            num_rows: Number of depth-of-book rows per side.
            wait_seconds: Time to wait for data to arrive.

        Returns:
            Dict with ``"bids"`` and ``"asks"`` lists.
        """
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

    def get_status_snapshot(self) -> dict[str, Any]:
        """Return compact connection status payload for UI polling."""
        return {
            "connected": self.is_connected(),
            "mode": self.get_connection_mode(),
            "env": self.get_environment(),
            "client_id": str(self.client_id),
            "account": self.get_account(),
        }
