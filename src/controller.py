import json
import logging
import math
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ib_insync import IB, Forex
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox

from controller_settings import SettingsMixin
from services.ib_client import IBClient
from services.market_data_engine import MarketDataEngine
from services.order_executor import OrderExecutor
from services.risk_engine import RiskEngine
from services.vol_engine import VolEngine
from ui.main_window import MainWindow

logger = logging.getLogger("controller")


UI_QUEUE_POLL_MS = 50
ENGINE_POLL_MS = 1000
ACCOUNT_SNAPSHOT_INTERVAL_S = 10.0
THREAD_JOIN_TIMEOUT_S = 5.0
FUT_MULTIPLIER = 125_000


class Controller(SettingsMixin):
    # Settings defaults inherited from SettingsMixin

    def __init__(self) -> None:
        """Initialize app state, services, and persisted settings."""
        self._project_root = Path(__file__).resolve().parents[1]
        self._settings_path = self._resolve_settings_path()
        app_settings = self._load_app_settings()
        status_settings = app_settings["status"]
        runtime_settings = app_settings["runtime"]

        self.host = status_settings["host"]
        self.port = status_settings["port"]
        self.client_id = status_settings["client_id"]
        self.client_roles = dict(status_settings.get("client_roles", {"market_data": 1, "vol_engine": 2, "risk_engine": 3}))
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
        self._ui_queue: queue.Queue = queue.Queue()
        self._ui_poll_timer: QTimer | None = None
        self._order_worker: OrderExecutor | None = None

        # Engine pool (Thread 1, 2, 3)
        self._market_engine: MarketDataEngine | None = None
        self._vol_engine: VolEngine | None = None
        self._risk_engine: RiskEngine | None = None
        self._vol_output_queue: queue.Queue = queue.Queue()
        self._risk_output_queue: queue.Queue = queue.Queue()
        self._engine_pool: list[threading.Thread] = []
        self._engine_poll_timer: QTimer | None = None

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

    def _global_connection_state(self, connecting: bool = False) -> str:
        """Return connection state text."""
        if self.ib_client.is_connected():
            return "connected"
        if connecting:
            return "connecting"
        return "disconnected"

    def _disconnect_client(self) -> None:
        """Disconnect the IB client and stop active subscriptions."""
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
        # Reset UI panels to default values
        if self.window is not None:
            self.window.portfolio_panel.reset()
            self._refresh_status(force=True)

    def _connect_client(self) -> tuple[bool, str]:
        """Connect the IB client and return (ok, error_message)."""
        if self.ib_client.is_connected():
            return True, ""
        ok = bool(self.ib_client.connect())
        if ok:
            return True, ""
        reason = self.ib_client.get_last_error_text() or "Unable to connect to IBKR."
        self._disconnect_client()
        return False, reason

    def _resolve_settings_path(self) -> Path:
        """Resolve settings path and migrate legacy location when needed."""
        config_dir = self._project_root / "config"
        config_path = config_dir / "status_panel_settings.json"
        legacy_path = self._project_root / "status_panel_settings.json"

        if config_path.exists():
            return config_path

        if legacy_path.exists():
            try:
                config_dir.mkdir(parents=True, exist_ok=True)
                config_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
                logger.info("Migrated settings to %s", config_path)
                return config_path
            except Exception as exc:
                logger.warning("Settings migration failed (%s -> %s): %s", legacy_path, config_path, exc)
                return legacy_path

        return config_path

    def _create_window(self) -> None:
        """Create and show the main dashboard window."""
        self.window = MainWindow.create_main_window(
            on_connect=self._start_connect,
            on_disconnect=self._disconnect_client,
            on_start_engine=self._start_live_streaming,
            on_stop_engine=self._stop_live_streaming,
            on_save_settings=self._open_settings,
            status_defaults={
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
                "market_symbol": self.market_symbol,
            },
        )
        self.window.resize(1500, 1000)
        self.window.show()

    def _log(self, message: str) -> None:
        """Send a message to the logs panel and logger."""
        logger.info(message)
        if self.window is not None:
            self.window.logs_panel.update({"message": message})

    def _setup_services(self) -> None:
        """Configure workers, signal wiring, and status timer."""
        if self.window is None:
            return

        self._setup_order_worker()
        self.window.order_ticket_panel.place_button.clicked.connect(self._queue_order_from_ticket)
        self.window.order_ticket_panel.order_preview_requested.connect(self._on_order_preview_requested)
        limit_update_btn = getattr(self.window.order_ticket_panel, "limit_price_update_button", None)
        if limit_update_btn is not None:
            try:
                limit_update_btn.clicked.connect(self._update_limit_price_from_market)
            except RuntimeError:
                pass
        # pnl_spot_panel has no signals to connect
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

        self.window.open_positions_panel.close_position_requested.connect(self._on_close_position)
        self.window.chart_panel.market_symbol_input.currentTextChanged.connect(self._on_market_symbol_changed)

        # Single QTimer polling the UI queue — only PyQt timer in the controller
        self._ui_poll_timer = QTimer(self.window)
        self._ui_poll_timer.setInterval(UI_QUEUE_POLL_MS)
        self._ui_poll_timer.timeout.connect(self._drain_ui_queue)
        self._ui_poll_timer.start()
        logger.info("QTimer _ui_poll_timer started (50ms)")

        self._refresh_status(force=True)

    # ── UI queue: single bridge from threads → main thread ──

    def _post_ui(self, callback: Any) -> None:
        """Post a callable to be executed on the main thread."""
        self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        """Drain all pending UI callbacks (called by the single QTimer)."""
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                logger.error("UI queue callback failed: %s", exc)

    def _setup_order_worker(self) -> None:
        """Create the order worker (plain class, no thread)."""
        if self._order_worker is not None:
            return
        self._order_worker = OrderExecutor(ib_client=self.ib_client)
        self._order_worker.start()

    def _is_market_worker_running(self) -> bool:
        """Return True when the engine pool is active."""
        return self._market_engine is not None

    def _stop_order_worker(self) -> None:
        """Stop order worker."""
        if self._order_worker is not None:
            self._order_worker.stop()
        self._order_worker = None

    # ── Engine Pool (Thread 1 + 2 + 3) ──

    def _start_engine_pool(self) -> None:
        """Create and start all 3 worker threads."""
        self._vol_output_queue = queue.Queue()
        self._risk_output_queue = queue.Queue()

        # Thread 1: Market Data (client_id=1, shared IB connection)
        self._market_engine = MarketDataEngine(
            ib_client=self.ib_client,
            ui_queue_post=self._post_ui,
            interval_ms=self.tick_interval_ms,
            snapshot_interval_s=ACCOUNT_SNAPSHOT_INTERVAL_S,
        )

        # Thread 3: Risk Engine (client_id=3, own IB connection)
        self._risk_engine = RiskEngine(
            output_queue=self._risk_output_queue,
            host=str(self.host), port=int(self.port), client_id=3,
        )

        # Thread 2: Vol Engine (client_id=2, own IB connection)
        # Check market status via a spot snapshot before starting
        market_open = self._check_market_open()
        if market_open:
            self._vol_engine = VolEngine(
                output_queue=self._vol_output_queue,
                host=str(self.host), port=int(self.port), client_id=2,
            )
            self._vol_engine.set_risk_engine(self._risk_engine)
            if self.window:
                self.window.vol_scanner_panel.set_status("computing")
        else:
            self._vol_engine = None
            self._log("[VOL] Market closed — Vol Engine not started")
            if self.window:
                self.window.vol_scanner_panel.set_status("market_closed")

        # Wire shared data: Thread 1 writes spot into Thread 3
        self._market_engine.set_risk_engine(self._risk_engine)

        # Market engine dispatches payloads to controller
        self._market_engine.on_payload = self._on_market_data_payload

        # Start threads
        self._engine_pool = [self._market_engine, self._risk_engine]
        if self._vol_engine is not None:
            self._engine_pool.append(self._vol_engine)
        for t in self._engine_pool:
            t.start()
        logger.info("Engine pool started: %s", [t.name for t in self._engine_pool])

        # QTimer 1s polls Thread 2 & 3 output queues + status refresh
        self._engine_poll_timer = QTimer(self.window)
        self._engine_poll_timer.setInterval(ENGINE_POLL_MS)
        self._engine_poll_timer.timeout.connect(self._poll_engine_queues)
        self._engine_poll_timer.start()

    def _stop_engine_pool(self) -> None:
        """Stop all worker threads and the poll timer. Idempotent."""
        if not self._engine_pool:
            return

        if self._engine_poll_timer is not None:
            self._engine_poll_timer.stop()
            self._engine_poll_timer = None

        for t in self._engine_pool:
            t.stop()
        for t in self._engine_pool:
            t.join(timeout=THREAD_JOIN_TIMEOUT_S)
        self._engine_pool.clear()
        self._market_engine = None
        self._vol_engine = None
        self._risk_engine = None
        if self.window:
            self.window.vol_scanner_panel.set_status("idle")
        logger.info("Engine pool stopped")

    def _poll_engine_queues(self) -> None:
        """Called by QTimer 1s. Drains vol + risk output queues, refreshes status."""
        # Vol engine results
        while True:
            try:
                result = self._vol_output_queue.get_nowait()
            except queue.Empty:
                break
            if result.get("type") == "vol_status" and self.window:
                self.window.vol_scanner_panel.set_status(result["status"])
                continue
            error = result.get("error")
            if error:
                self._log(f"[VOL] engine error: {error}")
                if self.window and "Market closed" in str(error):
                    self.window.vol_scanner_panel.set_status("market_closed")
            else:
                self._on_vol_result(result)

        # Risk engine results
        while True:
            try:
                result = self._risk_output_queue.get_nowait()
            except queue.Empty:
                break
            error = result.get("error")
            if error:
                self._log(f"[RISK] engine error: {error}")
            else:
                self._on_risk_result(result)

        self._refresh_status()

    # ── Engine result handlers ──

    def _on_vol_result(self, result: dict) -> None:
        """Handle vol engine output — update scanner, term structure, smile panels."""
        if self.window is None:
            return
        rows = result.get("scanner_rows", [])
        spot = result.get("spot", 0)
        logger.info("Vol scan complete: F=%.5f, %d tenors", spot, len(rows))
        self.window.vol_scanner_panel.set_status("ok")
        self.window.vol_scanner_panel.update(result)
        self._update_term_structure(result)
        self._update_smile_chart(result)

    def _on_risk_result(self, result: dict) -> None:
        """Handle risk engine output — update positions, greeks, PnL panels."""
        if self.window is None:
            return
        self.window.open_positions_panel.update({"open_positions": result.get("open_positions", [])})
        summary = result.get("summary", {})
        self.window.book_panel.update({"summary": summary})

        pnl_curve = result.get("pnl_curve")
        if pnl_curve:
            self.window.pnl_spot_panel.update(pnl_curve)


    def _update_term_structure(self, result: dict) -> None:
        """Build term structure payload from vol engine pillar_rows."""
        pillar_rows = result.get("pillar_rows", [])
        if not pillar_rows:
            return
        tenors = []
        iv_market = []
        sigma_fair = []
        rv = []
        for p in pillar_rows:
            atm = p.get("sigma_ATM_pct")
            if atm is None:
                continue
            tenors.append(p.get("tenor_label", ""))
            iv_market.append(atm)
            fair = p.get("sigma_fair_pct")
            sigma_fair.append(fair if fair is not None else atm)
            rv_val = p.get("RV_pct")
            rv.append(rv_val if rv_val is not None else 0)
        if tenors:
            self.window.term_structure_panel.update({
                "tenors": tenors,
                "iv_market": iv_market,
                "sigma_fair": sigma_fair,
                "rv": rv,
            })

    def _update_smile_chart(self, result: dict) -> None:
        """Pass smile data from vol engine to smile chart panel."""
        smile_data = result.get("smile_data", {})
        if smile_data:
            self.window.smile_chart_panel.update({"smiles": smile_data})

    def _check_market_open(self) -> bool:
        """Stream EURUSD briefly to determine if the market is open."""
        if not self.ib_client.is_connected():
            return False
        try:
            contract = Forex("EURUSD")
            ticker = self.ib_client.request_market_data(contract)
            if ticker is None:
                return False
            # Poll for up to 3 seconds waiting for a valid price
            for _ in range(6):
                self.ib_client.ib.sleep(0.5)
                bid = getattr(ticker, "bid", None)
                ask = getattr(ticker, "ask", None)
                valid_bid = bid is not None and isinstance(bid, (int, float)) and not math.isnan(bid) and bid > 0
                valid_ask = ask is not None and isinstance(ask, (int, float)) and not math.isnan(ask) and ask > 0
                if valid_bid or valid_ask:
                    logger.info("Market open check: bid=%s, ask=%s", bid, ask)
                    self.ib_client.ib.cancelMktData(contract)
                    return True
            logger.info("Market open check: no valid price after 3s (bid=%s, ask=%s)", bid, ask)
            self.ib_client.ib.cancelMktData(contract)
            return False
        except Exception as exc:
            logger.warning("Market open check failed: %s", exc)
            return False

    def _get_mid_spot(self) -> float | None:
        """Return mid spot price from cached bid/ask, or best available side."""
        bid = self._latest_bid
        ask = self._latest_ask
        if bid and ask and bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        return bid or ask

    def _on_market_data_payload(self, payload: Any) -> None:
        """Route market payload slices to their corresponding UI panels."""
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
                stop_button = getattr(status_panel, "stop_engine_button", None)
                if stop_button is not None and hasattr(stop_button, "click"):
                    stop_button.click()
                else:
                    self._stop_live_streaming()

        portfolio_payload = payload.get("portfolio_payload")
        if isinstance(portfolio_payload, dict):
            self.window.portfolio_panel.update(portfolio_payload)
            self._update_cash_balances_from_summary(portfolio_payload.get("summary"))
            self._refresh_order_ticket_market_context()

    def _refresh_account_snapshots(self) -> None:
        """Fetch account snapshot and update cash balances on connect."""
        if self.window is None or not self.ib_client.is_connected():
            return
        summary, _positions = self.ib_client.get_portfolio_snapshot()
        self._update_cash_balances_from_summary(summary)
        self._refresh_order_ticket_market_context()

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        """Parse numeric values from IB account snapshot fields."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _split_fx_symbol(symbol: str) -> tuple[str | None, str | None]:
        """Split six-letter FX symbol into (base, quote) currencies."""
        normalized = str(symbol).strip().upper()
        if len(normalized) < 6:
            return None, None
        return normalized[:3], normalized[3:6]

    def _update_latest_quote_from_ticks(self, ticks: list[Any]) -> None:
        """Update latest bid/ask cache from incoming tick payload list."""
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

    def _update_cash_balances_from_summary(self, summary: Any) -> None:
        """Update per-currency cash balances from account summary rows."""
        balances = self._extract_cash_balances(summary)
        if balances:
            self._cash_balances_by_currency = balances

    @staticmethod
    def _extract_cash_balances(summary: Any) -> dict[str, float]:
        """Extract best available per-currency cash balances from account summary rows."""
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

    def _compute_max_quantities_for_symbol(self, symbol: str) -> tuple[int | None, int | None]:
        """Compute max buy/sell quantities from quote prices and cached cash balances."""
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

    def _build_ticket_funding_snapshot(self, request: dict[str, Any]) -> dict[str, Any]:
        """Build funding snapshot for current ticket request (required/requested/available)."""
        symbol = str(request.get("symbol", "")).strip().upper()
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()
        try:
            quantity = int(request.get("quantity", request.get("volume", 0)))
        except (TypeError, ValueError):
            quantity = 0
        limit_price = self._parse_float(request.get("limit_price"))
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

    def _resolve_buy_price_for_funds(
        self,
        symbol: str,
        order_type: str,
        limit_price: float | None,
    ) -> tuple[float | None, str]:
        """Resolve the quote price used to validate BUY-side funding."""
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

    def _validate_order_funds(self, request: dict[str, Any]) -> tuple[bool, str]:
        """Validate that the portfolio has enough currency funds for the ticket request."""
        symbol = str(request.get("symbol", "")).strip().upper()
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()
        try:
            quantity = int(request.get("quantity", 0))
        except (TypeError, ValueError):
            quantity = 0
        limit_price = self._parse_float(request.get("limit_price"))

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

    def _refresh_order_ticket_market_context(self, *_: Any) -> None:
        """Refresh order-ticket market context rows (quote and max quantities)."""
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
    def _should_auto_stop_live_stream(messages: list[Any]) -> bool:
        """Detect final no-tick warning that should auto-stop live streaming."""
        return any(
            "[warn][market_data]" in str(item).lower() and "no ticks received (test 3/3)" in str(item).lower()
            for item in messages
        )

    def _on_market_data_failed(self, message: str) -> None:
        """Surface market worker failures to the log panel."""
        if self.window is None:
            return
        self.window.logs_panel.update({"message": f"[WARN][market_data] worker error: {message}"})

    def _on_order_preview_requested(self, order: dict) -> None:
        """Book (Preview) clicked — preview → dialog → place if confirmed."""
        if self._order_worker is None or not self._order_worker._running:
            self._log("Order executor is not running.", level="error")
            return
        if not self.ib_client.is_connected():
            self._log("Not connected to IBKR.", level="error")
            return

        self.window.order_ticket_panel.set_feedback("Loading preview...", level="info")
        try:
            instrument = order.get("instrument", "")

            # Resolve tenor → expiry from cached JSON for options
            if instrument == "Option" and "tenor" in order and "expiry" not in order:
                order = dict(order)  # don't mutate original
                order["expiry"] = self._resolve_tenor_to_expiry(order["tenor"])
                if not order["expiry"]:
                    self.window.order_ticket_panel.set_feedback(
                        f"Unknown tenor: {order['tenor']}", level="error")
                    return

            if instrument == "Future":
                preview = self._order_worker.preview_future_order(order)
            elif instrument == "Option":
                preview = self._order_worker.preview_option_order(order)
            else:
                preview = self._order_worker.preview_order(order)

            if not preview.get("ok", False):
                self.window.order_ticket_panel.set_feedback(
                    preview.get("message", "Preview failed"), level="error")
                self._log(f"[FAIL][preview] {preview.get('message', '')}")
                return

            # Delta hedge preview if requested
            hedge_preview = None
            hedge_order = None
            if instrument == "Option" and order.get("delta_hedge"):
                hedge_order, hedge_preview = self._build_hedge_preview(order, preview)

            self.window.order_ticket_panel.set_feedback("", level="info")

            self.window.order_ticket_panel.show_preview_dialog(
                preview,
                on_confirmed=lambda: self._place_confirmed_order(order, hedge_order),
                hedge_preview=hedge_preview,
            )
        except Exception as exc:
            self.window.order_ticket_panel.set_feedback(str(exc), level="error")
            self._log(f"[ERROR][order] {exc}")

    def _build_hedge_preview(self, opt_order: dict, opt_preview: dict) -> tuple[dict | None, dict | None]:
        """Compute delta hedge future order and its preview."""
        try:
            # Get raw delta from option preview (unit delta × qty × multiplier)
            delta_usd = opt_preview.get("delta_usd")
            if delta_usd is None or delta_usd == "--":
                self._log("[WARN][hedge] No delta in option preview, skipping hedge")
                return None, None

            delta_usd = float(delta_usd)
            # Each future = spot x FUT_MULTIPLIER USD delta
            mid = self._get_mid_spot() or 1.0
            fut_delta_per_contract = mid * FUT_MULTIPLIER
            hedge_qty = round(abs(delta_usd) / fut_delta_per_contract)
            if hedge_qty < 1:
                hedge_qty = 1

            # Opposite side to neutralize delta
            hedge_side = "SELL" if delta_usd > 0 else "BUY"

            hedge_order = {
                "instrument": "Future",
                "fut_symbol": "EUR",
                "symbol": "EURUSD",
                "side": hedge_side,
                "order_type": "MKT",
                "quantity": hedge_qty,
                "volume": hedge_qty,
                "multiplier": FUT_MULTIPLIER,
                "limit_price": 0.0,
                "reference_price": mid,
                "use_bracket": False,
                "take_profit": None, "stop_loss": None,
                "take_profit_pct": None, "stop_loss_pct": None,
                "rr_ratio": None,
            }

            hedge_preview = self._order_worker.preview_future_order(hedge_order)
            if not hedge_preview.get("ok", False):
                self._log(f"[WARN][hedge] Hedge preview failed: {hedge_preview.get('message')}")
                return None, None

            return hedge_order, hedge_preview
        except Exception as exc:
            self._log(f"[ERROR][hedge] {exc}")
            return None, None

    def _place_confirmed_order(self, order: dict, hedge_order: dict | None = None) -> None:
        """Called when user clicks Send Order in the preview dialog."""
        if self._order_worker is None or not self._order_worker._running:
            self._log("Order executor is not running.", level="error")
            return
        try:
            order = dict(order)
            if order.get("instrument") == "Option" and "tenor" in order and "expiry" not in order:
                order["expiry"] = self._resolve_tenor_to_expiry(order["tenor"])
            instrument = order.get("instrument", "")

            # 1. Place main order
            if instrument == "Future":
                result = self._order_worker.place_future_order(order)
            elif instrument == "Option":
                result = self._order_worker.place_option_order(order)
            else:
                result = self._order_worker.place_order(order)
            ok = result.get("ok", False)
            msg = result.get("message", "")
            self._log(f"[{'OK' if ok else 'FAIL'}][order] {msg}")

            # 2. Place hedge if requested and main order succeeded
            if ok and hedge_order is not None:
                self._log("[INFO][hedge] Placing delta hedge...")
                hedge_result = self._order_worker.place_future_order(hedge_order)
                hedge_ok = hedge_result.get("ok", False)
                hedge_msg = hedge_result.get("message", "")
                self._log(f"[{'OK' if hedge_ok else 'FAIL'}][hedge] {hedge_msg}")
                msg = f"{msg} | Hedge: {hedge_msg}"
                ok = ok and hedge_ok

            level = "success" if ok else "error"
            self.window.order_ticket_panel.set_feedback(msg, level=level)
        except Exception as exc:
            self.window.order_ticket_panel.set_feedback(str(exc), level="error")
            self._log(f"[ERROR][order] {exc}")

    def _on_close_position(self, pos: dict) -> None:
        """Close a position by placing a reverse MKT order."""
        if self._order_worker is None or not self._order_worker._running:
            self._log("[WARN][close] Order worker is not running.")
            return
        if not self.ib_client.is_connected():
            self._log("[WARN][close] Not connected to IBKR.")
            return

        sec_type = pos.get("sec_type", "")
        side = pos.get("side", "")
        qty = pos.get("qty", 0)
        close_side = "SELL" if side == "BUY" else "BUY"

        try:
            if sec_type == "FUT":
                request = {"side": close_side, "quantity": qty, "expiry": pos.get("expiry", "")}
                result = self._order_worker.place_future_order(request)
            elif sec_type == "FOP":
                strike_raw = pos.get("strike", "0")
                try:
                    strike_val = float(str(strike_raw).replace(",", ""))
                except ValueError:
                    strike_val = 0.0
                request = {
                    "side": close_side,
                    "quantity": qty,
                    "right": pos.get("right", ""),
                    "strike": strike_val,
                    "expiry": pos.get("expiry", ""),
                }
                result = self._order_worker.place_option_order(request)
            else:
                symbol = pos.get("symbol", "EURUSD")
                request = {
                    "symbol": symbol,
                    "side": close_side,
                    "quantity": qty,
                    "order_type": "MKT",
                }
                result = self._order_worker.place_order(request)

            ok = result.get("ok", False)
            msg = result.get("message", "")
            self._log(f"[{'OK' if ok else 'FAIL'}][close] {msg}")
            if ok and self._risk_engine is not None:
                self._risk_engine.request_refresh()
        except Exception as exc:
            self._log(f"[ERROR][close] {exc}")

    def _queue_order_from_ticket(self) -> None:
        """Validate ticket input and queue order with user confirmation dialog."""
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

    def _preview_order_from_ticket(self) -> None:
        """Validate and enqueue an order preview request."""
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

    def _cancel_single_order(self, trade: Any) -> None:
        """Cancel a single order from the orders panel."""
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

    def _on_order_result(self, payload: Any) -> None:
        """Render order worker responses in ticket/log panels."""
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

    def _sync_order_ticket_action_buttons(self, connected: bool, order_thread_running: bool) -> None:
        """Sync preview/place button enabled state with connection/thread/funding checks."""
        if self.window is None:
            return
        order_ticket_panel = getattr(self.window, "order_ticket_panel", None)
        if order_ticket_panel is None:
            return
        connecting = bool(getattr(self, "_connecting", False))
        can_act = bool(connected and not connecting and order_thread_running)
        funds_ok = getattr(self, "_ticket_funds_ok", None)
        can_place = can_act and funds_ok is not False
        place_button = getattr(order_ticket_panel, "place_button", None)
        if place_button is not None and hasattr(place_button, "setEnabled"):
            place_button.setEnabled(can_place)

    def _on_order_failed(self, message: str) -> None:
        """Render fatal order worker errors."""
        if self.window is None:
            return
        self.window.order_ticket_panel.update({"message": f"Order worker failure: {message}", "level": "error"})
        self.window.logs_panel.update({"message": f"[ERROR][execution] order worker failure: {message}"})

    @staticmethod
    def _is_valid_market_price(value: Any) -> bool:
        """Return True when value can be used as a finite market price."""
        if value is None:
            return False
        if not isinstance(value, (int, float)):
            return False
        return not math.isnan(float(value))

    def _update_limit_price_from_market(self) -> None:
        """Refresh LMT price from latest bid/ask according to selected side."""
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

    def _refresh_status(self, payload: dict[str, Any] | None = None, force: bool = False) -> None:
        """Refresh status panel state and button availability."""
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

    def _start_connect(self) -> None:
        """Start connection flow using current settings."""
        if self._connecting or self.window is None:
            return
        if self.ib_client.is_connected():
            return

        self._stop_engine_pool()
        self.ib_client.stop_live_streaming()
        self._disconnect_client()

        try:
            status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
            self._apply_status_settings(status_settings)
        except Exception as exc:
            self._last_connect_error = str(exc)
            logger.warning("Invalid settings: %s", self._last_connect_error)
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

    def _on_connect_result(self, connected: bool, error_message: str) -> None:
        """Handle connect worker completion payload."""
        self._connecting = False
        self._last_connect_error = str(error_message or "").strip()
        if self.window is not None:
            if connected:
                self.window.logs_panel.update({"message": "[INFO][connection] connected to IBKR"})
                self._refresh_account_snapshots()
                self._discover_option_chains()
            else:
                message = self._last_connect_error or "IB connection failed."
                self.window.logs_panel.update({"message": f"[ERROR][connection] {message}"})
                self.window.order_ticket_panel.update({"message": message, "level": "error"})
        self._refresh_status(force=True)

    def _on_market_symbol_changed(self, symbol: str) -> None:
        """Handle market symbol change — restart streaming if active."""
        normalized = str(symbol).strip().upper()
        if not normalized or normalized == self.market_symbol:
            return
        self.market_symbol = normalized
        if self._is_market_worker_running():
            self._stop_live_streaming()
            self._start_live_streaming()

    def _start_live_streaming(self) -> None:
        """Start IB live market stream and polling worker."""
        if self.window is None:
            return
        if self._is_market_worker_running():
            return

        connected = self.ib_client.is_connected()
        if not connected:
            self._refresh_status(force=True)
            return

        try:
            status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
            self._apply_status_settings(status_settings)
        except Exception as exc:
            self._last_connect_error = str(exc)
            logger.warning("Settings validation failed: %s", self._last_connect_error)
            self._refresh_status(force=True)
            return

        started = self.ib_client.start_live_streaming(self.market_symbol)
        if not started:
            logger.warning("Start streaming failed: %s", self.ib_client.get_last_error_text())
            self._refresh_status(force=True)
            return

        self._start_engine_pool()
        self._refresh_status(force=True)
        if self.window is not None:
            self.window.logs_panel.update(
                {"message": f"[INFO][market_data] live stream started for {self.market_symbol}"}
            )

    def _resolve_tenor_to_expiry(self, tenor: str) -> str:
        """Read config/fop_expiries.json and return expiry YYYYMMDD for a tenor."""
        path = self._project_root / "config" / "fop_expiries.json"
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get(tenor, "")
        except Exception:
            return ""

    # _open_settings, _on_settings_saved → SettingsMixin (controller_settings.py)

    def _discover_option_chains(self) -> None:
        """Discover FOP expiries/strikes on Connect. Saves 2 JSONs + populates Strike combo.

        Iterates over all quarterly EUR futures to collect all FOP expiries.
        """
        if self.window is None or not self.ib_client.is_connected():
            return
        try:
            from datetime import date, datetime, timedelta

            from ib_insync import Contract

            # 1. Get all quarterly EUR futures
            fut = Contract()
            fut.symbol = "EUR"
            fut.secType = "FUT"
            fut.exchange = "CME"
            fut.currency = "USD"

            details = self.ib_client.ib.reqContractDetails(fut)
            if not details:
                self._log("[WARN][options] No EUR futures found")
                return

            today = date.today()
            min_exp = (today + timedelta(days=7)).strftime("%Y%m%d")
            quarterly = [
                d.contract for d in details
                if d.contract.lastTradeDateOrContractMonth >= min_exp
                and int(d.contract.lastTradeDateOrContractMonth[4:6]) in {3, 6, 9, 12}
            ]
            quarterly.sort(key=lambda c: c.lastTradeDateOrContractMonth)
            self._log(f"[INFO][options] Found {len(quarterly)} quarterly futures")

            # 2. For each future, get option params — track strikes per expiry
            expiry_strikes: dict[str, set[float]] = {}

            for fut_c in quarterly:
                params = self.ib_client.ib.reqSecDefOptParams(
                    "EUR", "CME", "FUT", fut_c.conId)
                euu = [p for p in params if p.tradingClass == "EUU"]
                for p in euu:
                    for exp in p.expirations:
                        if exp not in expiry_strikes:
                            expiry_strikes[exp] = set()
                        expiry_strikes[exp].update(p.strikes)
                pass

            sorted_expiries = sorted(expiry_strikes.keys())
            self._log(f"[INFO][options] Total: {len(sorted_expiries)} expiries")

            # 3. Build tenor→date mapping (like list_fop_expiries.py)
            now = datetime.now()
            tenor_map: dict[str, str] = {}
            for exp in sorted_expiries:
                exp_date = datetime.strptime(exp, "%Y%m%d")
                days = (exp_date - now).days
                if days < 1:
                    continue
                months = round(days / 30.44)
                if months < 1:
                    label = f"{days}D"
                elif months < 12:
                    label = f"{months}M"
                else:
                    years = months // 12
                    remainder = months % 12
                    label = f"{years}Y" if remainder == 0 else f"{years}Y{remainder}M"
                if label not in tenor_map:
                    tenor_map[label] = exp

            # Build per-tenor strikes
            tenor_strikes: dict[str, list[float]] = {}
            for t, exp in tenor_map.items():
                tenor_strikes[t] = sorted(expiry_strikes.get(exp, set()))

            self._log(f"[INFO][options] {len(tenor_map)} tenors")
            pass

            # 4. Save fop_expiries.json (tenor → date)
            config_dir = self._project_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            expiries_path = config_dir / "fop_expiries.json"
            expiries_path.write_text(json.dumps(tenor_map, indent=2), encoding="utf-8")
            self._log(f"[INFO][options] Saved {expiries_path}")

            # 5. Save fop_strikes.json (strikes per tenor)
            strikes_path = config_dir / "fop_strikes.json"
            strikes_path.write_text(json.dumps(tenor_strikes, indent=2), encoding="utf-8")
            self._log(f"[INFO][options] Saved {strikes_path}")

            # 6. Populate panel with per-tenor strikes
            self.window.order_ticket_panel.set_option_chains(tenor_strikes)
        except Exception as exc:
            self._log(f"[ERROR][options] Chain discovery failed: {exc}")

    def _stop_live_streaming(self) -> None:
        """Stop live stream subscription and market polling worker."""
        self._stop_engine_pool()
        self.ib_client.stop_live_streaming()
        if self.window is not None:
            self.window.chart_panel.update({"clear": True})
            self._latest_bid = None
            self._latest_ask = None
            self._refresh_order_ticket_market_context()
        self._refresh_status(force=True)
        if self.window is not None:
            self.window.logs_panel.update({"message": "[INFO][market_data] live stream stopped"})

    # Settings methods → SettingsMixin (controller_settings.py)

    def _start_server_time_sync(self) -> None:
        """Synchronize server time if supported (direct call, no thread)."""
        if not self.ib_client.supports_server_time():
            return
        time_text, latency_text = self.ib_client.get_server_time_and_latency()
        self._server_time_text = time_text
        self._latency_ms_text = latency_text
        self._last_server_sync_sec = int(time.time())
        self._refresh_status(force=True)

    def _shutdown_services(self) -> None:
        """Stop workers, subscriptions, and IB connection on exit."""
        self._connecting = False
        self._stop_engine_pool()
        self._stop_order_worker()
        if self._ui_poll_timer is not None:
            self._ui_poll_timer.stop()
            self._ui_poll_timer = None
        self._disconnect_client()

    def run(self) -> int:
        """Start the asyncio + Qt integrated event loop."""
        self._create_window()
        self._setup_services()
        self.window.window_closed.connect(self._on_app_quit)
        try:
            self.ib.run()
        except (SystemExit, KeyboardInterrupt):
            pass
        finally:
            self._shutdown_services()
        return 0

    def _on_app_quit(self) -> None:
        """Stop the asyncio event loop when the last window is closed."""
        self._shutdown_services()
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon(loop.stop)
