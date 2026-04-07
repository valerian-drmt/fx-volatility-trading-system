import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox
from ib_insync import Forex, IB

from client_roles import default_client_roles
from services.ib_client import IBClient
from services.market_data_worker import MarketDataWorker
from services.order_worker import OrderWorker
from ui.main_window import MainWindow


class Controller:
    DEFAULT_STATUS_SETTINGS = {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 3,
        "client_roles": default_client_roles(),
        "readonly": False,
        "market_symbol": "EURUSD",
    }
    DEFAULT_RUNTIME_SETTINGS = {
        "tick_interval_ms": 100,
        "snapshot_interval_ms": 2000,
    }

    # Initialize app state, services, and persisted settings.
    def __init__(self) -> None:
        self._project_root = Path(__file__).resolve().parents[1]
        self._settings_path = self._resolve_settings_path()
        app_settings = self._load_app_settings()
        status_settings = app_settings["status"]
        runtime_settings = app_settings["runtime"]

        self.host = status_settings["host"]
        self.port = status_settings["port"]
        self.client_id = status_settings["client_id"]
        self.client_roles = dict(status_settings.get("client_roles", default_client_roles()))
        self.readonly = status_settings["readonly"]
        self.market_symbol = status_settings["market_symbol"]
        self.tick_interval_ms = runtime_settings["tick_interval_ms"]
        self.snapshot_interval_ms = runtime_settings["snapshot_interval_ms"]

        self.app = QApplication(sys.argv)
        self.ib = IB()
        self.ib_client = IBClient(
            ib=self.ib,
            ticker=None,
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            readonly=False,
        )

        self.window: MainWindow | None = None
        self._status_timer: QTimer | None = None
        self._market_data_poll_timer: QTimer | None = None
        self._market_data_worker: MarketDataWorker | None = None
        self._order_worker: OrderWorker | None = None

        self._last_status_sec = None
        self._last_server_sync_sec = None
        self._server_time_text = "--"
        self._latency_ms_text = "--"
        self._connecting = False
        self._last_connect_error = ""
        self._latest_bid: float | None = None
        self._latest_ask: float | None = None
        self._cash_balances_by_currency: dict[str, float] = {}
        self._ticket_funds_ok: bool | None = None

    # Return connection state text.
    def _global_connection_state(self, connecting: bool = False) -> str:
        if self.ib_client.is_connected():
            return "connected"
        if connecting:
            return "connecting"
        return "disconnected"

    # Disconnect the IB client and stop active subscriptions.
    def _disconnect_client(self) -> None:
        try:
            self.ib_client.stop_live_streaming()
        except Exception:
            pass
        try:
            self.ib_client.cancel_account_summary()
        except Exception:
            pass
        if self.ib.isConnected():
            try:
                self.ib.disconnect()
            except Exception:
                pass

    # Connect the IB client and return (ok, error_message).
    def _connect_client(self) -> tuple[bool, str]:
        if self.ib_client.is_connected():
            return True, ""
        ok = bool(self.ib_client.connect())
        if ok:
            return True, ""
        reason = self.ib_client.get_last_error_text() or "Unable to connect to IBKR."
        self._disconnect_client()
        return False, reason

    # Resolve settings path and migrate legacy location when needed.
    def _resolve_settings_path(self) -> Path:
        config_dir = self._project_root / "config"
        config_path = config_dir / "status_panel_settings.json"
        legacy_path = self._project_root / "status_panel_settings.json"

        if config_path.exists():
            return config_path

        if legacy_path.exists():
            try:
                config_dir.mkdir(parents=True, exist_ok=True)
                config_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"Migrated settings to {config_path}")
                return config_path
            except Exception as exc:
                print(f"Settings migration warning ({legacy_path} -> {config_path}): {exc}")
                return legacy_path

        return config_path

    # Create and show the main dashboard window.
    def _create_window(self) -> None:
        self.window = MainWindow.create_main_window(
            on_connect=self._start_connect,
            on_start_live_streaming=self._start_live_streaming,
            on_stop_live_streaming=self._stop_live_streaming,
            on_save_settings=self._save_app_settings,
            status_defaults={
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
                "market_symbol": self.market_symbol,
            },
        )
        self.window.resize(1500, 1000)
        self.window.show()

    # Configure workers, signal wiring, and status timer.
    def _setup_services(self) -> None:
        if self.window is None:
            return

        self._setup_order_worker()
        self.window.order_ticket_panel.place_button.clicked.connect(self._queue_order_from_ticket)
        self.window.order_ticket_panel.preview_button.clicked.connect(self._preview_order_from_ticket)
        self.window.order_ticket_panel.limit_price_update_button.clicked.connect(self._update_limit_price_from_market)
        self.window.orders_panel.cancel_order_requested.connect(self._cancel_single_order)
        symbol_input = getattr(self.window.order_ticket_panel, "symbol_input", None)
        if symbol_input is not None and hasattr(symbol_input, "currentTextChanged"):
            symbol_input.currentTextChanged.connect(self._refresh_order_ticket_market_context)
        side_combo = getattr(self.window.order_ticket_panel, "side_combo", None)
        if side_combo is not None and hasattr(side_combo, "currentTextChanged"):
            side_combo.currentTextChanged.connect(self._refresh_order_ticket_market_context)
        order_type_combo = getattr(self.window.order_ticket_panel, "order_type_combo", None)
        if order_type_combo is not None and hasattr(order_type_combo, "currentTextChanged"):
            order_type_combo.currentTextChanged.connect(self._refresh_order_ticket_market_context)
        qty_input = getattr(self.window.order_ticket_panel, "qty_input", None)
        if qty_input is not None and hasattr(qty_input, "valueChanged"):
            qty_input.valueChanged.connect(self._refresh_order_ticket_market_context)
        limit_price_input = getattr(self.window.order_ticket_panel, "limit_price_input", None)
        if limit_price_input is not None and hasattr(limit_price_input, "valueChanged"):
            limit_price_input.valueChanged.connect(self._refresh_order_ticket_market_context)

        self.window.chart_panel.market_symbol_input.currentTextChanged.connect(self._on_market_symbol_changed)

        self._status_timer = QTimer(self.window)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._refresh_status(force=True)

    # Create the order worker (plain class, no thread).
    def _setup_order_worker(self) -> None:
        if self._order_worker is not None:
            return
        self._order_worker = OrderWorker(ib_client=self.ib_client)
        self._order_worker.start()

    # Return True when the market worker is active.
    def _is_market_worker_running(self) -> bool:
        return self._market_data_worker is not None

    # Start market data polling via QTimer on the main thread.
    def _start_market_data_worker(self) -> None:
        if self._market_data_worker is not None:
            return
        self._market_data_worker = MarketDataWorker(
            ib_client=self.ib_client,
            interval_ms=self.tick_interval_ms,
            snapshot_interval_ms=self.snapshot_interval_ms,
        )
        self._market_data_poll_timer = QTimer(self.window)
        self._market_data_poll_timer.setInterval(self.tick_interval_ms)
        self._market_data_poll_timer.timeout.connect(self._poll_market_data)
        self._market_data_poll_timer.start()

    # Poll market data worker and route payload to UI.
    def _poll_market_data(self) -> None:
        if self._market_data_worker is None:
            return
        try:
            payload = self._market_data_worker.poll_once()
            self._on_market_data_payload(payload)
        except Exception as exc:
            self._on_market_data_failed(str(exc))

    # Stop market data polling and clear worker.
    def _stop_market_data_worker(self) -> None:
        if self._market_data_poll_timer is not None:
            self._market_data_poll_timer.stop()
            self._market_data_poll_timer = None
        self._market_data_worker = None

    # Stop order worker.
    def _stop_order_worker(self) -> None:
        if self._order_worker is not None:
            self._order_worker.stop()
        self._order_worker = None

    # Route market payload slices to their corresponding UI panels.
    def _on_market_data_payload(self, payload: Any) -> None:
        if self.window is None or not isinstance(payload, dict):
            return

        status_payload = payload.get("status")
        if isinstance(status_payload, dict):
            self._refresh_status(payload=status_payload)

        ticks = payload.get("ticks")
        if isinstance(ticks, list) and ticks:
            self.window.chart_panel.update({"ticks": ticks})
            self._update_latest_quote_from_ticks(ticks)
            self._refresh_order_ticket_market_context()

        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            self.window.logs_panel.update({"messages": [str(item) for item in messages]})
            if self._should_auto_stop_live_stream(messages):
                status_panel = getattr(self.window, "status_panel", None)
                stop_button = getattr(status_panel, "stop_live_stream_button", None)
                if stop_button is not None and hasattr(stop_button, "click"):
                    stop_button.click()
                else:
                    self._stop_live_streaming()

        orders_payload = payload.get("orders_payload")
        if isinstance(orders_payload, dict):
            self.window.orders_panel.update(orders_payload)

        portfolio_payload = payload.get("portfolio_payload")
        if isinstance(portfolio_payload, dict):
            self.window.portfolio_panel.update(portfolio_payload)
            self._update_cash_balances_from_summary(portfolio_payload.get("summary"))
            self._refresh_order_ticket_market_context()

    # Collect a best-effort open-orders list for account-level display.
    def _collect_open_orders_for_panel(self, orders_client: Any) -> list[Any]:
        open_orders: Any = []
        request_all_open_orders = getattr(orders_client, "request_all_open_orders", None)
        if callable(request_all_open_orders):
            try:
                open_orders = request_all_open_orders() or []
            except Exception:
                open_orders = []

        if not isinstance(open_orders, list) or not open_orders:
            get_open_orders_snapshot = getattr(orders_client, "get_open_orders_snapshot", None)
            if callable(get_open_orders_snapshot):
                try:
                    open_orders = get_open_orders_snapshot() or []
                except Exception:
                    open_orders = []

        return open_orders if isinstance(open_orders, list) else []

    # Collect recent order activity for display (executions first, then fills).
    def _collect_recent_orders_for_panel(self, orders_client: Any) -> list[Any]:
        recent_orders: Any = []
        get_recent_fills_snapshot = getattr(orders_client, "get_recent_fills_snapshot", None)
        if callable(get_recent_fills_snapshot):
            try:
                recent_orders = get_recent_fills_snapshot() or []
            except Exception:
                recent_orders = []

        if isinstance(recent_orders, list) and recent_orders:
            return recent_orders

        get_executions_snapshot = getattr(orders_client, "get_executions_snapshot", None)
        if callable(get_executions_snapshot):
            try:
                recent_orders = get_executions_snapshot() or []
            except Exception:
                recent_orders = []

        if not isinstance(recent_orders, list) or not recent_orders:
            get_fills_snapshot = getattr(orders_client, "get_fills_snapshot", None)
            if callable(get_fills_snapshot):
                try:
                    recent_orders = get_fills_snapshot() or []
                except Exception:
                    recent_orders = []

        return recent_orders if isinstance(recent_orders, list) else []

    # Fetch and render one-shot orders/portfolio snapshots for a connected session.
    def _refresh_account_snapshots(self) -> None:
        if self.window is None:
            return

        if not self.ib_client.is_connected():
            return
        orders_client = self.ib_client
        portfolio_client = self.ib_client
        open_orders = self._collect_open_orders_for_panel(orders_client)
        fills = self._collect_recent_orders_for_panel(orders_client)
        summary, positions = portfolio_client.get_portfolio_snapshot()

        self.window.orders_panel.update({"open_orders": open_orders, "fills": fills})
        self.window.portfolio_panel.update({"summary": summary, "positions": positions})
        self._update_cash_balances_from_summary(summary)
        self._refresh_order_ticket_market_context()

    @staticmethod
    # Parse numeric values from IB account snapshot fields.
    def _parse_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    # Split six-letter FX symbol into (base, quote) currencies.
    def _split_fx_symbol(symbol: str) -> tuple[str | None, str | None]:
        normalized = str(symbol).strip().upper()
        if len(normalized) < 6:
            return None, None
        return normalized[:3], normalized[3:6]

    # Update latest bid/ask cache from incoming tick payload list.
    def _update_latest_quote_from_ticks(self, ticks: list[Any]) -> None:
        latest_bid = None
        latest_ask = None
        for tick in reversed(ticks):
            if not isinstance(tick, dict):
                continue
            if latest_bid is None:
                bid_value = tick.get("bid")
                if self._is_valid_market_price(bid_value):
                    latest_bid = float(bid_value)
            if latest_ask is None:
                ask_value = tick.get("ask")
                if self._is_valid_market_price(ask_value):
                    latest_ask = float(ask_value)
            if latest_bid is not None and latest_ask is not None:
                break
        if latest_bid is not None:
            self._latest_bid = latest_bid
        if latest_ask is not None:
            self._latest_ask = latest_ask

    # Update per-currency cash balances from account summary rows.
    def _update_cash_balances_from_summary(self, summary: Any) -> None:
        balances = self._extract_cash_balances(summary)
        if balances:
            self._cash_balances_by_currency = balances

    @staticmethod
    # Extract best available per-currency cash balances from account summary rows.
    def _extract_cash_balances(summary: Any) -> dict[str, float]:
        if not isinstance(summary, list):
            return {}

        tag_priority = {
            "TotalCashBalance": 3,
            "CashBalance": 2,
            "AvailableFunds": 1,
        }
        balances: dict[str, tuple[int, float]] = {}
        for item in summary:
            tag = str(getattr(item, "tag", "")).strip()
            priority = tag_priority.get(tag)
            if priority is None:
                continue
            currency = str(getattr(item, "currency", "")).strip().upper()
            if currency in {"", "BASE", "TOTAL"}:
                continue
            value = Controller._parse_float(getattr(item, "value", None))
            if value is None or math.isnan(value):
                continue
            current = balances.get(currency)
            if current is None or priority > current[0]:
                balances[currency] = (priority, float(value))

        return {currency: value for currency, (_priority, value) in balances.items()}

    # Compute max buy/sell quantities from quote prices and cached cash balances.
    def _compute_max_quantities_for_symbol(self, symbol: str) -> tuple[int | None, int | None]:
        base_currency, quote_currency = self._split_fx_symbol(symbol)
        if base_currency is None or quote_currency is None:
            return None, None

        max_buy_qty = None
        available_quote = self._cash_balances_by_currency.get(quote_currency)
        ask_price = self._latest_ask
        if available_quote is not None and ask_price is not None and ask_price > 0:
            max_buy_qty = max(0, int(available_quote / ask_price))

        max_sell_qty = None
        available_base = self._cash_balances_by_currency.get(base_currency)
        if available_base is not None:
            max_sell_qty = max(0, int(available_base))

        return max_buy_qty, max_sell_qty

    # Build funding snapshot for current ticket request (required/requested/available).
    def _build_ticket_funding_snapshot(self, request: dict[str, Any]) -> dict[str, Any]:
        symbol = str(request.get("symbol", "")).strip().upper()
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()
        try:
            quantity = int(request.get("quantity", request.get("volume", 0)))
        except (TypeError, ValueError):
            quantity = 0
        limit_price = self._parse_float(request.get("limit_price", None))
        base_currency, quote_currency = self._split_fx_symbol(symbol)

        requested_volume = float(quantity) if quantity > 0 else None
        requested_currency = quote_currency if side == "SELL" else base_currency
        required_volume = None
        required_currency = None

        if base_currency is not None and quote_currency is not None and quantity > 0 and side in {"BUY", "SELL"}:
            if side == "BUY":
                required_currency = quote_currency
                if order_type == "LMT" and limit_price is not None and limit_price > 0:
                    required_volume = float(quantity) * float(limit_price)
                elif order_type == "MKT":
                    stream_symbol = str(self.market_symbol).strip().upper()
                    if stream_symbol == symbol and self._is_valid_market_price(self._latest_ask):
                        required_volume = float(quantity) * float(self._latest_ask)
            else:
                requested_currency = quote_currency
                requested_volume = None
                if order_type == "LMT" and limit_price is not None and limit_price > 0:
                    requested_volume = float(quantity) * float(limit_price)
                elif order_type == "MKT":
                    stream_symbol = str(self.market_symbol).strip().upper()
                    if stream_symbol == symbol and self._is_valid_market_price(self._latest_bid):
                        requested_volume = float(quantity) * float(self._latest_bid)
                required_currency = base_currency
                required_volume = float(quantity)

        available_required_volume = None
        if required_currency is not None:
            available_required_volume = self._cash_balances_by_currency.get(required_currency)

        funds_ok: bool | None = None
        if required_volume is not None and required_currency is not None:
            if available_required_volume is None:
                funds_ok = False
            else:
                funds_ok = float(available_required_volume) + 1e-9 >= float(required_volume)

        return {
            "requested_volume": requested_volume,
            "requested_currency": requested_currency,
            "required_volume": required_volume,
            "required_currency": required_currency,
            "available_required_volume": available_required_volume,
            "funds_ok": funds_ok,
        }

    # Resolve the quote price used to validate BUY-side funding.
    def _resolve_buy_price_for_funds(
        self,
        symbol: str,
        order_type: str,
        limit_price: float | None,
    ) -> tuple[float | None, str]:
        normalized_type = str(order_type).strip().upper()
        if normalized_type == "LMT":
            if limit_price is None or limit_price <= 0:
                return None, "Limit price must be > 0 for BUY LMT orders."
            return float(limit_price), ""

        stream_symbol = str(self.market_symbol).strip().upper()
        if stream_symbol == symbol and self._is_valid_market_price(self._latest_ask):
            return float(self._latest_ask), ""

        market_client = self.ib_client
        if not market_client.is_connected():
            return None, "Connect to IBKR and start streaming before sending BUY market orders."
        snapshot = market_client.get_market_snapshot(Forex(symbol), wait_seconds=0.35)

        if isinstance(snapshot, dict):
            ask = self._parse_float(snapshot.get("ask"))
            if ask is not None and ask > 0:
                return float(ask), ""
            bid = self._parse_float(snapshot.get("bid"))
            if bid is not None and bid > 0:
                return float(bid), ""
        return None, f"Cannot validate BUY funds for {symbol}: no live quote available."

    # Validate that the portfolio has enough currency funds for the ticket request.
    def _validate_order_funds(self, request: dict[str, Any]) -> tuple[bool, str]:
        symbol = str(request.get("symbol", "")).strip().upper()
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()
        try:
            quantity = int(request.get("quantity", 0))
        except (TypeError, ValueError):
            quantity = 0
        limit_price = self._parse_float(request.get("limit_price", None))

        base_currency, quote_currency = self._split_fx_symbol(symbol)
        if base_currency is None or quote_currency is None or side not in {"BUY", "SELL"} or quantity <= 0:
            return True, ""

        if not self._cash_balances_by_currency:
            return False, "Portfolio balances unavailable. Wait for account snapshot before placing orders."

        if side == "BUY":
            available_quote = self._cash_balances_by_currency.get(quote_currency)
            if available_quote is None:
                return False, f"No {quote_currency} balance found in portfolio."
            quote_price, error_message = self._resolve_buy_price_for_funds(symbol, order_type, limit_price)
            if quote_price is None:
                return False, error_message or f"Cannot validate BUY funds for {symbol}."
            required_quote = float(quantity) * float(quote_price)
            if float(available_quote) + 1e-9 < required_quote:
                return (
                    False,
                    (
                        f"Insufficient {quote_currency} funds: need {required_quote:,.2f} {quote_currency} "
                        f"to BUY {quantity:,} {base_currency}, available {float(available_quote):,.2f}."
                    ),
                )
            return True, ""

        available_base = self._cash_balances_by_currency.get(base_currency)
        if available_base is None:
            return False, f"No {base_currency} balance found in portfolio."
        required_base = float(quantity)
        if float(available_base) + 1e-9 < required_base:
            return (
                False,
                (
                    f"Insufficient {base_currency} funds: need {required_base:,.0f} {base_currency} "
                    f"to SELL {quantity:,} {base_currency}, available {float(available_base):,.2f}."
                ),
            )
        return True, ""

    # Refresh order-ticket market context rows (quote and max quantities).
    def _refresh_order_ticket_market_context(self, *_: Any) -> None:
        if self.window is None:
            return
        order_ticket_panel = getattr(self.window, "order_ticket_panel", None)
        if order_ticket_panel is None or not hasattr(order_ticket_panel, "update"):
            return
        symbol_input = getattr(order_ticket_panel, "symbol_input", None)
        ticket_symbol = symbol_input.currentText().strip().upper() if symbol_input is not None else ""
        stream_symbol = str(self.market_symbol).strip().upper()

        bid = None
        ask = None
        max_buy_qty = None
        max_sell_qty = None
        if ticket_symbol and stream_symbol and ticket_symbol == stream_symbol:
            bid = self._latest_bid
            ask = self._latest_ask
            max_buy_qty, max_sell_qty = self._compute_max_quantities_for_symbol(ticket_symbol)

        if hasattr(order_ticket_panel, "get_order_request"):
            try:
                request = order_ticket_panel.get_order_request()
            except Exception:
                request = {
                    "symbol": ticket_symbol,
                    "side": "BUY",
                    "order_type": "MKT",
                    "quantity": 0,
                    "volume": 0,
                    "limit_price": 0.0,
                }
        else:
            request = {
                "symbol": ticket_symbol,
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 0,
                "volume": 0,
                "limit_price": 0.0,
            }
        funding_snapshot = self._build_ticket_funding_snapshot(request)
        self._ticket_funds_ok = funding_snapshot.get("funds_ok")

        order_ticket_panel.update(
            {
                "bid": bid,
                "ask": ask,
                "max_buy_qty": max_buy_qty,
                "max_sell_qty": max_sell_qty,
                "requested_volume": funding_snapshot["requested_volume"],
                "requested_currency": funding_snapshot["requested_currency"],
                "required_volume": funding_snapshot["required_volume"],
                "required_currency": funding_snapshot["required_currency"],
                "available_required_volume": funding_snapshot["available_required_volume"],
                "funds_ok": funding_snapshot["funds_ok"],
            }
        )

        connecting = bool(getattr(self, "_connecting", False))
        connected = self._global_connection_state(connecting=connecting) == "connected"
        order_worker_running = self._order_worker is not None and self._order_worker._running
        self._sync_order_ticket_action_buttons(connected=connected, order_thread_running=order_worker_running)

    @staticmethod
    # Detect final no-tick warning that should auto-stop live streaming.
    def _should_auto_stop_live_stream(messages: list[Any]) -> bool:
        return any(
            "[warn][market_data]" in str(item).lower() and "no ticks received (test 3/3)" in str(item).lower()
            for item in messages
        )

    # Surface market worker failures to the log panel.
    def _on_market_data_failed(self, message: str) -> None:
        if self.window is None:
            return
        self.window.logs_panel.update({"message": f"[WARN][market_data] worker error: {message}"})

    # Validate and enqueue a new order request from the ticket panel.
    def _queue_order_from_ticket(self) -> None:
        if self.window is None or self._order_worker is None:
            return
        if self._order_worker is None or not self._order_worker._running:
            self.window.order_ticket_panel.update({"message": "Order thread is not running.", "level": "error"})
            return

        request = self.window.order_ticket_panel.get_order_request()
        connected = self.ib_client.is_connected()
        if not connected:
            self.window.order_ticket_panel.update({"message": "Connect to IBKR before sending orders.", "level": "error"})
            return
        funds_ok, funds_error = self._validate_order_funds(request)
        if not funds_ok:
            self.window.order_ticket_panel.update({"message": funds_error, "level": "error"})
            self.window.logs_panel.update({"message": f"[WARN][execution] {funds_error}"})
            return

        symbol = request.get("symbol", "")
        side = request.get("side", "")
        qty = request.get("quantity", "")
        order_type = request.get("order_type", "")
        limit_price = request.get("limit_price", "")
        use_bracket = bool(request.get("use_bracket", False))
        take_profit_pct = request.get("take_profit_pct", None)
        stop_loss_pct = request.get("stop_loss_pct", None)
        order_desc = f"{side} {qty} {symbol} {order_type}"
        if str(order_type).upper() == "LMT":
            order_desc += f" @ {limit_price}"
        if use_bracket and take_profit_pct is not None and stop_loss_pct is not None:
            order_desc += f" BRACKET TP={float(take_profit_pct):.3f}% SL={float(stop_loss_pct):.3f}%"

        confirm_text = f"Send order?\n{order_desc}"
        msg_box = QMessageBox(self.window)
        msg_box.setWindowTitle("Confirm Order")
        msg_box.setText(confirm_text)
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)

        def _on_confirm(button: Any) -> None:
            role = msg_box.buttonRole(button)
            if role != QMessageBox.YesRole:
                self.window.order_ticket_panel.update({"message": "Order cancelled by user.", "level": "info"})
                self.window.logs_panel.update({"message": f"[INFO][execution] user cancelled {order_desc}"})
                return
            try:
                self.window.order_ticket_panel.update({"message": "Order queued for execution.", "level": "info"})
                self.window.logs_panel.update({"message": f"[INFO][execution] queued {order_desc}"})
                result = self._order_worker.place_order(request)
                self._on_order_result(result)
            except Exception as exc:
                self.window.order_ticket_panel.update({"message": f"Order error: {exc}", "level": "error"})

        msg_box.buttonClicked.connect(_on_confirm)
        msg_box.show()

    # Validate and enqueue an order preview request.
    def _preview_order_from_ticket(self) -> None:
        if self.window is None or self._order_worker is None:
            return
        if self._order_worker is None or not self._order_worker._running:
            self.window.order_ticket_panel.update({"message": "Order thread is not running.", "level": "error"})
            return

        request = self.window.order_ticket_panel.get_order_request()
        connected = self.ib_client.is_connected()
        if not connected:
            self.window.order_ticket_panel.update({"message": "Connect to IBKR before preview.", "level": "error"})
            return

        symbol = request.get("symbol", "")
        side = request.get("side", "")
        qty = request.get("quantity", "")
        order_type = request.get("order_type", "")
        self.window.order_ticket_panel.update({"message": "Preview queued for execution thread.", "level": "info"})
        self.window.logs_panel.update({"message": f"[INFO][execution] preview queued {side} {qty} {symbol} {order_type}"})
        result = self._order_worker.preview_order(request)
        self._on_order_result(result)

    # Cancel a single order from the orders panel.
    def _cancel_single_order(self, trade: Any) -> None:
        if self.window is None:
            return
        if not self.ib_client.is_connected():
            self.window.logs_panel.update({"message": "[WARN][cancel] not connected to IBKR"})
            return

        # For bracket child orders, cancel the parent instead (children follow).
        order_obj = getattr(trade, "order", trade)
        parent_id = getattr(order_obj, "parentId", 0)
        target = trade
        if parent_id:
            open_trades = self.ib_client.get_open_orders_snapshot()
            for t in open_trades:
                t_order = getattr(t, "order", t)
                if getattr(t_order, "orderId", None) == parent_id:
                    target = t
                    break

        # Skip if already inactive.
        target_order = getattr(target, "order", target)
        target_status = getattr(getattr(target, "orderStatus", None), "status", "")
        if str(target_status).lower() in {"cancelled", "inactive", "apicancelled", "filled", "pendingcancel"}:
            self.window.logs_panel.update({"message": f"[INFO][cancel] order already {target_status}"})
            return

        ok = self.ib_client.cancel_order(target_order)
        if ok:
            self.window.logs_panel.update({"message": "[INFO][cancel] order cancel requested"})
        else:
            error = self.ib_client.get_last_error_text() or "cancel failed"
            self.window.logs_panel.update({"message": f"[WARN][cancel] {error}"})

    # Render order worker responses in ticket/log panels.
    def _on_order_result(self, payload: Any) -> None:
        if self.window is None or not isinstance(payload, dict):
            return

        ok = bool(payload.get("ok", False))
        kind = str(payload.get("kind", "order")).strip().lower()
        message = str(payload.get("message", "Order response received."))
        if ok:
            if kind == "preview":
                level = "preview"
            elif kind in {"order", "cancel_all"}:
                level = "success"
            else:
                level = "info"
            log_level = "INFO"
        else:
            level = "error"
            log_level = "ERROR"
        if kind == "preview":
            source = "execution_preview"
        elif kind == "cancel_all":
            source = "execution_cancel"
        else:
            source = "execution"

        self.window.order_ticket_panel.update({"message": message, "level": level})
        self.window.logs_panel.update({"message": f"[{log_level}][{source}] {message}"})

    # Sync preview/place button enabled state with connection/thread/funding checks.
    def _sync_order_ticket_action_buttons(self, connected: bool, order_thread_running: bool) -> None:
        if self.window is None:
            return
        order_ticket_panel = getattr(self.window, "order_ticket_panel", None)
        if order_ticket_panel is None:
            return
        connecting = bool(getattr(self, "_connecting", False))
        can_preview = bool(connected and not connecting and order_thread_running)
        funds_ok = getattr(self, "_ticket_funds_ok", None)
        can_place = can_preview and funds_ok is not False
        preview_button = getattr(order_ticket_panel, "preview_button", None)
        place_button = getattr(order_ticket_panel, "place_button", None)
        if preview_button is not None and hasattr(preview_button, "setEnabled"):
            preview_button.setEnabled(can_preview)
        if place_button is not None and hasattr(place_button, "setEnabled"):
            place_button.setEnabled(can_place)

    # Render fatal order worker errors.
    def _on_order_failed(self, message: str) -> None:
        if self.window is None:
            return
        self.window.order_ticket_panel.update({"message": f"Order worker failure: {message}", "level": "error"})
        self.window.logs_panel.update({"message": f"[ERROR][execution] order worker failure: {message}"})

    @staticmethod
    # Return True when value can be used as a finite market price.
    def _is_valid_market_price(value: Any) -> bool:
        if value is None:
            return False
        if not isinstance(value, (int, float)):
            return False
        return not math.isnan(float(value))

    # Refresh LMT price from latest bid/ask according to selected side.
    def _update_limit_price_from_market(self) -> None:
        if self.window is None:
            return

        request = self.window.order_ticket_panel.get_order_request()
        symbol = str(request.get("symbol", "")).strip().upper()
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()

        if order_type != "LMT":
            self.window.order_ticket_panel.update({"message": "Select LMT to update limit price.", "level": "info"})
            return

        market_client = self.ib_client
        connected = market_client.is_connected()
        bid, ask = market_client.get_latest_bid_ask() if connected else (None, None)

        if not connected:
            self.window.order_ticket_panel.update(
                {"message": "Connect to IBKR before updating limit price.", "level": "error"}
            )
            return

        stream_symbol = str(self.market_symbol).strip().upper()
        if symbol and stream_symbol and symbol != stream_symbol:
            message = (
                f"Live quote is {stream_symbol}. Select the same symbol in Order Ticket "
                "to update limit price from market."
            )
            self.window.order_ticket_panel.update({"message": message, "level": "error"})
            self.window.logs_panel.update({"message": f"[WARN][execution] {message}"})
            return

        if side == "BUY":
            candidate = ask if self._is_valid_market_price(ask) else bid
        else:
            candidate = bid if self._is_valid_market_price(bid) else ask

        if not self._is_valid_market_price(candidate):
            message = f"No live quote available for {stream_symbol or symbol or '--'}."
            self.window.order_ticket_panel.update({"message": message, "level": "error"})
            self.window.logs_panel.update({"message": f"[WARN][execution] {message}"})
            return

        limit_price = float(candidate)
        self.window.order_ticket_panel.set_limit_price(limit_price)
        self.window.order_ticket_panel.update({"message": f"Limit price updated to {limit_price:.8f}.", "level": "success"})
        self.window.logs_panel.update(
            {
                "message": (
                    f"[INFO][execution] limit price refreshed for {symbol or stream_symbol} "
                    f"({side}) -> {limit_price:.8f}"
                )
            }
        )

    # Refresh status panel state and button availability.
    def _refresh_status(self, payload: dict[str, Any] | None = None, force: bool = False) -> None:
        if self.window is None:
            return

        now_sec = int(time.time())
        if not force and self._last_status_sec == now_sec:
            return
        self._last_status_sec = now_sec

        if isinstance(payload, dict):
            status = payload
        else:
            status = self.ib_client.get_status_snapshot()

        connection_state = self._global_connection_state(connecting=self._connecting)

        connected = connection_state == "connected"
        pipeline_running = self._is_market_worker_running()
        order_thread_running = self._order_worker is not None and self._order_worker._running

        if not connected:
            self._latency_ms_text = "--"
            self._server_time_text = "--"

        self.window.status_panel.update(
            {
                "connection_state": connection_state,
                "mode": str(status.get("mode", "--")),
                "env": str(status.get("env", "--")),
                "client_id": str(status.get("client_id", "--")),
                "account": str(status.get("account", "--")),
                "latency": self._latency_ms_text,
                "server_time": self._server_time_text,
                "connecting": self._connecting,
                "pipeline_running": pipeline_running,
            }
        )
        self._sync_order_ticket_action_buttons(connected=connected, order_thread_running=order_thread_running)
        if hasattr(self.window.order_ticket_panel, "set_limit_price_update_available"):
            self.window.order_ticket_panel.set_limit_price_update_available(connected and not self._connecting)

        if connected and (self._last_server_sync_sec is None or now_sec - self._last_server_sync_sec >= 10):
            self._start_server_time_sync()

    # Start connection flow using current settings.
    def _start_connect(self) -> None:
        if self._connecting or self.window is None:
            return
        if self.ib_client.is_connected():
            return

        self._stop_market_data_worker()
        self.ib_client.stop_live_streaming()
        self._disconnect_client()

        try:
            status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
            self._apply_status_settings(status_settings)
        except Exception as exc:
            self._last_connect_error = str(exc)
            print(f"Invalid settings: {self._last_connect_error}")
            self._refresh_status(force=True)
            return

        self._connecting = True
        self._last_connect_error = ""
        self._refresh_status(force=True)

        connected = False
        error_message = ""
        try:
            connected, error_message = self._connect_client()
        except Exception as exc:
            error_message = str(exc)
        self._on_connect_result(connected, error_message)

    # Handle connect worker completion payload.
    def _on_connect_result(self, connected: bool, error_message: str) -> None:
        self._connecting = False
        self._last_connect_error = str(error_message or "").strip()
        if self.window is not None:
            if connected:
                self.window.logs_panel.update({"message": "[INFO][connection] connected to IBKR"})
                self._refresh_account_snapshots()
            else:
                message = self._last_connect_error or "IB connection failed."
                self.window.logs_panel.update({"message": f"[ERROR][connection] {message}"})
                self.window.order_ticket_panel.update({"message": message, "level": "error"})
        self._refresh_status(force=True)

    # Handle market symbol change — restart streaming if active.
    def _on_market_symbol_changed(self, symbol: str) -> None:
        normalized = str(symbol).strip().upper()
        if not normalized or normalized == self.market_symbol:
            return
        self.market_symbol = normalized
        if self._is_market_worker_running():
            self._stop_live_streaming()
            self._start_live_streaming()

    # Start IB live market stream and polling worker.
    def _start_live_streaming(self) -> None:
        if self.window is None:
            return
        if self._is_market_worker_running():
            return

        connected = self.ib_client.is_connected()
        print(f"[DEBUG][streaming] all clients connected: {connected}")
        if not connected:
            self._refresh_status(force=True)
            return

        try:
            status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
            print(f"[DEBUG][streaming] settings validated: symbol={status_settings.get('market_symbol')}")
            self._apply_status_settings(status_settings)
        except Exception as exc:
            self._last_connect_error = str(exc)
            print(f"[DEBUG][streaming] settings validation failed: {self._last_connect_error}")
            self._refresh_status(force=True)
            return

        print(f"[DEBUG][streaming] calling start_live_streaming({self.market_symbol!r}) on market client...")
        started = self.ib_client.start_live_streaming(self.market_symbol)
        print(f"[DEBUG][streaming] start_live_streaming returned: {started}")
        if not started:
            error_text = self.ib_client.get_last_error_text()
            print(f"[DEBUG][streaming] start failed, last error: {error_text!r}")
            self._refresh_status(force=True)
            return

        self._start_market_data_worker()
        print(f"[DEBUG][streaming] worker running: {self._is_market_worker_running()}")
        self._refresh_status(force=True)
        if self.window is not None:
            self.window.logs_panel.update(
                {"message": f"[INFO][market_data] live stream started for {self.market_symbol}"}
            )

    # Stop live stream subscription and market polling worker.
    def _stop_live_streaming(self) -> None:
        self._stop_market_data_worker()
        self.ib_client.stop_live_streaming()
        if self.window is not None:
            self.window.chart_panel.update({"clear": True})
            self._latest_bid = None
            self._latest_ask = None
            self._refresh_order_ticket_market_context()
        self._refresh_status(force=True)
        if self.window is not None:
            self.window.logs_panel.update({"message": "[INFO][market_data] live stream stopped"})

    # Read status settings from UI controls (or current state fallback).
    def _read_status_settings_from_panel(self) -> dict[str, Any]:
        if self.window is None:
            roles = dict(getattr(self, "client_roles", default_client_roles()))
            return {
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
                "client_roles": roles,
                "readonly": False,
                "market_symbol": self.market_symbol,
            }

        panel = self.window.status_panel
        roles = dict(getattr(self, "client_roles", default_client_roles()))
        roles["dashboard"] = int(panel.client_id_input.value())
        return {
            "host": panel.host_input.text().strip(),
            "port": int(panel.port_input.value()),
            "client_id": int(roles["dashboard"]),
            "client_roles": roles,
            "readonly": False,
            "market_symbol": self.window.chart_panel.market_symbol_input.currentText().strip().upper(),
        }

    # Apply validated status settings to controller and IB client.
    def _apply_status_settings(self, settings: dict[str, Any]) -> None:
        self.host = str(settings["host"])
        self.port = int(settings["port"])
        self.client_id = int(settings["client_id"])
        self.client_roles = dict(settings.get("client_roles", default_client_roles()))
        self.readonly = False
        self.market_symbol = str(settings["market_symbol"]).upper()

        self.ib_client.host = self.host
        self.ib_client.port = self.port
        self.ib_client.client_id = self.client_id
        self.ib_client.readonly = False

    # Load and validate persisted app settings with fallback defaults.
    def _load_app_settings(self) -> dict[str, Any]:
        defaults = self._default_app_settings()
        if not self._settings_path.exists():
            print(f"Settings file missing. Creating defaults at {self._settings_path}")
            self._write_full_app_settings(defaults)
            return defaults

        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
            validated = self._validate_app_settings(raw)
            return validated
        except Exception as exc:
            print(f"Invalid settings detected ({self._settings_path}): {exc}")
            print("Resetting to safe defaults.")
            self._write_full_app_settings(defaults)
            return defaults

    @staticmethod
    # Validate whole app settings and normalize legacy payloads.
    def _validate_app_settings(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Settings payload must be a JSON object")

        status_payload = raw.get("status")
        if isinstance(status_payload, dict):
            normalized_status = dict(status_payload)
        else:
            normalized_status = dict(raw)

        if "market_symbol" not in normalized_status:
            legacy_streaming = raw.get("live_streaming")
            if isinstance(legacy_streaming, dict):
                normalized_status["market_symbol"] = legacy_streaming.get("market_symbol", "EURUSD")

        runtime_payload = raw.get("runtime")
        if runtime_payload is None:
            runtime_payload = {}

        return {
            "status": Controller._validate_status_settings(normalized_status),
            "runtime": Controller._validate_runtime_settings(runtime_payload),
        }

    @staticmethod
    # Validate runtime timing settings and enforce safe bounds.
    def _validate_runtime_settings(raw: dict[str, Any]) -> dict[str, int]:
        if not isinstance(raw, dict):
            raise ValueError("Runtime settings payload must be a JSON object")

        tick_interval_ms = int(raw.get("tick_interval_ms", Controller.DEFAULT_RUNTIME_SETTINGS["tick_interval_ms"]))
        snapshot_interval_ms = int(
            raw.get("snapshot_interval_ms", Controller.DEFAULT_RUNTIME_SETTINGS["snapshot_interval_ms"])
        )
        if tick_interval_ms < 25:
            raise ValueError("Runtime setting 'tick_interval_ms' must be >= 25")
        if snapshot_interval_ms < 250:
            raise ValueError("Runtime setting 'snapshot_interval_ms' must be >= 250")
        if snapshot_interval_ms < tick_interval_ms:
            raise ValueError("Runtime setting 'snapshot_interval_ms' must be >= tick_interval_ms")

        return {
            "tick_interval_ms": tick_interval_ms,
            "snapshot_interval_ms": snapshot_interval_ms,
        }

    @staticmethod
    # Validate status/connection settings and normalize fields.
    def _validate_status_settings(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Settings payload must be a JSON object")

        required = ("host", "port")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"Missing settings keys: {', '.join(missing)}")

        host = str(raw["host"]).strip()
        if not host:
            raise ValueError("Settings 'host' cannot be empty")

        symbol = str(raw.get("market_symbol", "EURUSD")).strip().upper()
        if not symbol:
            raise ValueError("Settings 'market_symbol' cannot be empty")

        raw_roles = raw.get("client_roles")
        default_roles = default_client_roles()
        if isinstance(raw_roles, dict):
            roles = {
                "order_worker": int(raw_roles.get("order_worker", default_roles["order_worker"])),
                "market_data": int(raw_roles.get("market_data", default_roles["market_data"])),
                "dashboard": int(raw_roles.get("dashboard", raw.get("client_id", default_roles["dashboard"]))),
            }
        else:
            roles = dict(default_roles)

        ids = [int(roles["order_worker"]), int(roles["market_data"]), int(roles["dashboard"])]
        if len(set(ids)) != len(ids):
            raise ValueError("Client role ids must be distinct (order_worker, market_data, dashboard).")

        return {
            "host": host,
            "port": int(raw["port"]),
            "client_id": int(roles["dashboard"]),
            "client_roles": roles,
            "readonly": False,
            "market_symbol": symbol,
        }

    # Persist current status and runtime settings to disk.
    def _save_app_settings(self) -> None:
        status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
        self._apply_status_settings(status_settings)
        runtime_settings = self._validate_runtime_settings(
            {
                "tick_interval_ms": self.tick_interval_ms,
                "snapshot_interval_ms": self.snapshot_interval_ms,
            }
        )
        self._write_app_settings(status_settings, runtime_settings)

    @staticmethod
    # Return default app settings payload.
    def _default_app_settings() -> dict[str, dict[str, Any]]:
        status_defaults = dict(Controller.DEFAULT_STATUS_SETTINGS)
        status_defaults["client_roles"] = dict(default_client_roles())
        return {
            "status": status_defaults,
            "runtime": dict(Controller.DEFAULT_RUNTIME_SETTINGS),
        }

    # Validate and write a full app settings payload.
    def _write_full_app_settings(self, app_settings: dict[str, Any]) -> None:
        validated = self._validate_app_settings(app_settings)
        self._write_app_settings(validated["status"], validated["runtime"])

    # Write split status/runtime settings payload to disk.
    def _write_app_settings(self, status_settings: dict[str, Any], runtime_settings: dict[str, Any]) -> None:
        app_settings = {
            "status": status_settings,
            "runtime": runtime_settings,
        }
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(json.dumps(app_settings, indent=2), encoding="utf-8")
            print(f"Saved settings to {self._settings_path}")
        except Exception as exc:
            print(f"Failed to save settings: {exc}")

    # Synchronize server time if supported (direct call, no thread).
    def _start_server_time_sync(self) -> None:
        if not self.ib_client.supports_server_time():
            return
        time_text, latency_text = self.ib_client.get_server_time_and_latency()
        self._server_time_text = time_text
        self._latency_ms_text = latency_text
        self._last_server_sync_sec = int(time.time())
        self._refresh_status(force=True)

    # Stop workers, subscriptions, and IB connection on exit.
    def _shutdown_services(self) -> None:
        if self._status_timer is not None:
            self._status_timer.stop()
        self._connecting = False

        self._stop_market_data_worker()
        self._stop_order_worker()

        self._disconnect_client()

    # Start the asyncio + Qt integrated event loop.
    def run(self) -> int:
        self._create_window()
        self._setup_services()
        self.app.lastWindowClosed.connect(self._on_app_quit)
        try:
            self.ib.run()
        except (SystemExit, KeyboardInterrupt):
            pass
        finally:
            self._shutdown_services()
        return 0

    # Stop the asyncio event loop when the last window is closed.
    def _on_app_quit(self) -> None:
        self._shutdown_services()
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon(loop.stop)
