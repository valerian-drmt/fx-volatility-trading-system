import json
import sys
import time
from pathlib import Path
from threading import RLock
from typing import Any

from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox
from ib_insync import IB

from services.ib_client import IBClient
from services.market_data_worker import MarketDataWorker
from services.order_worker import OrderWorker
from ui.main_window import MainWindow


class ServerTimeWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(str, str)

    # Initialize async server-time worker dependencies.
    def __init__(self, ib_client: IBClient, io_lock: RLock) -> None:
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock

    # Query IB server time and emit the result pair.
    def run(self) -> None:
        with self.io_lock:
            time_text, latency_text = self.ib_client.get_server_time_and_latency()
        self.result.emit(time_text, latency_text)


class ConnectWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(bool, str)

    # Initialize async connection worker dependencies.
    def __init__(self, ib_client: IBClient, io_lock: RLock) -> None:
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock

    # Attempt IB connection and emit success/error status.
    def run(self) -> None:
        error_message = ""
        connected = False
        try:
            with self.io_lock:
                connected = bool(self.ib_client.connect())
                if not connected:
                    error_message = self.ib_client.get_last_error_text() or "Unable to connect to IBKR."
        except Exception as exc:
            error_message = str(exc)
        self.result.emit(connected, error_message)


class Controller:
    DEFAULT_STATUS_SETTINGS = {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 1,
        "readonly": True,
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
        self.readonly = status_settings["readonly"]
        self.market_symbol = status_settings["market_symbol"]
        self.tick_interval_ms = runtime_settings["tick_interval_ms"]
        self.snapshot_interval_ms = runtime_settings["snapshot_interval_ms"]

        self.app = QApplication(sys.argv)
        self.ib = IB()
        self._io_lock = RLock()
        self.ib_client = IBClient(
            ib=self.ib,
            ticker=None,
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            readonly=self.readonly,
        )

        self.window: MainWindow | None = None
        self._status_timer: QTimer | None = None
        self._server_time_worker: ServerTimeWorker | None = None
        self._connect_worker: ConnectWorker | None = None

        self._market_data_thread: QtCore.QThread | None = None
        self._market_data_worker: MarketDataWorker | None = None
        self._order_thread: QtCore.QThread | None = None
        self._order_worker: OrderWorker | None = None

        self._last_status_sec = None
        self._last_server_sync_sec = None
        self._server_time_text = "--"
        self._latency_ms_text = "--"
        self._connecting = False
        self._last_connect_error = ""

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
                "readonly": self.readonly,
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
        self._setup_market_data_worker()
        self.window.order_ticket_panel.place_button.clicked.connect(self._queue_order_from_ticket)
        self.window.order_ticket_panel.preview_button.clicked.connect(self._preview_order_from_ticket)
        self.window.order_ticket_panel.cancel_all_button.clicked.connect(self._cancel_all_orders_from_ticket)

        self._status_timer = QTimer(self.window)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._refresh_status(force=True)

    # Prepare market-data thread/worker wiring without starting it.
    def _setup_market_data_worker(self) -> None:
        if self.window is None:
            return
        if self._market_data_thread is not None and self._market_data_thread.isRunning():
            return

        self._market_data_thread = None
        self._market_data_worker = None

        thread = QtCore.QThread(self.window)
        worker = MarketDataWorker(
            ib_client=self.ib_client,
            io_lock=self._io_lock,
            interval_ms=self.tick_interval_ms,
            snapshot_interval_ms=self.snapshot_interval_ms,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_market_data_thread_finished)
        worker.payload_ready.connect(self._on_market_data_payload)
        worker.failed.connect(self._on_market_data_failed)
        self._market_data_thread = thread
        self._market_data_worker = worker

    # Start the order worker thread and connect result signals.
    def _setup_order_worker(self) -> None:
        if self.window is None or self._order_thread is not None:
            return
        thread = QtCore.QThread(self.window)
        worker = OrderWorker(ib_client=self.ib_client, io_lock=self._io_lock)
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        thread.finished.connect(worker.deleteLater)
        worker.order_result.connect(self._on_order_result)
        worker.failed.connect(self._on_order_failed)
        thread.start()
        self._order_thread = thread
        self._order_worker = worker

    # Return True when the market worker thread is active.
    def _is_market_worker_running(self) -> bool:
        return (
            self._market_data_thread is not None
            and self._market_data_thread.isRunning()
            and self._market_data_worker is not None
        )

    # Start market polling worker if it is not already running.
    def _start_market_data_worker(self) -> None:
        self._setup_market_data_worker()
        if self._market_data_thread is None or self._market_data_thread.isRunning():
            return
        self._market_data_thread.start()

    # Stop market polling worker and clear thread references.
    def _stop_market_data_worker(self) -> None:
        thread = self._market_data_thread
        worker = self._market_data_worker
        if thread is None or not thread.isRunning():
            self._market_data_thread = None
            self._market_data_worker = None
            return

        if worker is not None:
            QtCore.QMetaObject.invokeMethod(worker, "stop", QtCore.Qt.BlockingQueuedConnection)
        thread.quit()
        thread.wait(1500)
        if not thread.isRunning():
            self._market_data_thread = None
            self._market_data_worker = None

    # Clear market worker references after thread termination.
    def _on_market_data_thread_finished(self) -> None:
        self._market_data_thread = None
        self._market_data_worker = None

    # Stop order worker thread and clear references.
    def _stop_order_worker(self) -> None:
        thread = self._order_thread
        worker = self._order_worker
        if thread is None:
            return

        if worker is not None and thread.isRunning():
            QtCore.QMetaObject.invokeMethod(worker, "stop", QtCore.Qt.BlockingQueuedConnection)
        if thread.isRunning():
            thread.quit()
            thread.wait(1500)

        self._order_worker = None
        self._order_thread = None

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
            self.window.logs_panel.update({"ticks": ticks})

        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            self.window.logs_panel.update({"messages": [str(item) for item in messages]})

        orders_payload = payload.get("orders_payload")
        if isinstance(orders_payload, dict):
            self.window.orders_panel.update(orders_payload)

        portfolio_payload = payload.get("portfolio_payload")
        if isinstance(portfolio_payload, dict):
            self.window.portfolio_panel.update(portfolio_payload)

    # Surface market worker failures to the log panel.
    def _on_market_data_failed(self, message: str) -> None:
        if self.window is None:
            return
        self.window.logs_panel.update({"message": f"[WARN][market_data] worker error: {message}"})

    # Validate and enqueue a new order request from the ticket panel.
    def _queue_order_from_ticket(self) -> None:
        if self.window is None or self._order_worker is None:
            return
        if self._order_thread is None or not self._order_thread.isRunning():
            self.window.order_ticket_panel.update({"message": "Order thread is not running.", "level": "error"})
            return

        request = self.window.order_ticket_panel.get_order_request()
        with self._io_lock:
            connected = self.ib_client.is_connected()
        if not connected:
            self.window.order_ticket_panel.update({"message": "Connect to IBKR before sending orders.", "level": "error"})
            return
        if self.readonly:
            self.window.order_ticket_panel.update(
                {
                    "message": "Read-only mode is enabled. Disable it in settings before placing orders.",
                    "level": "error",
                }
            )
            self.window.logs_panel.update({"message": "[WARN][execution] blocked order: read-only mode enabled"})
            return

        symbol = request.get("symbol", "")
        side = request.get("side", "")
        qty = request.get("quantity", "")
        order_type = request.get("order_type", "")
        limit_price = request.get("limit_price", "")
        take_profit = request.get("take_profit", None)
        stop_loss = request.get("stop_loss", None)
        order_desc = f"{side} {qty} {symbol} {order_type}"
        if str(order_type).upper() == "LMT":
            order_desc += f" @ {limit_price}"
        if take_profit is not None and stop_loss is not None:
            order_desc += f" TP={take_profit} SL={stop_loss}"

        confirm_text = f"Send order?\n{order_desc}"
        confirm = QMessageBox.question(
            self.window,
            "Confirm Order",
            confirm_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            self.window.order_ticket_panel.update({"message": "Order cancelled by user.", "level": "info"})
            self.window.logs_panel.update({"message": f"[INFO][execution] user cancelled {order_desc}"})
            return

        self.window.order_ticket_panel.update({"message": "Order queued for execution thread.", "level": "info"})
        self.window.logs_panel.update({"message": f"[INFO][execution] queued {order_desc}"})
        self._order_worker.enqueue_order.emit(request)

    # Validate and enqueue an order preview request.
    def _preview_order_from_ticket(self) -> None:
        if self.window is None or self._order_worker is None:
            return
        if self._order_thread is None or not self._order_thread.isRunning():
            self.window.order_ticket_panel.update({"message": "Order thread is not running.", "level": "error"})
            return

        request = self.window.order_ticket_panel.get_order_request()
        with self._io_lock:
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
        self._order_worker.enqueue_preview.emit(request)

    # Confirm and enqueue a cancel-all request.
    def _cancel_all_orders_from_ticket(self) -> None:
        if self.window is None or self._order_worker is None:
            return
        if self._order_thread is None or not self._order_thread.isRunning():
            self.window.order_ticket_panel.update({"message": "Order thread is not running.", "level": "error"})
            return
        with self._io_lock:
            connected = self.ib_client.is_connected()
        if not connected:
            self.window.order_ticket_panel.update({"message": "Connect to IBKR before cancel requests.", "level": "error"})
            return
        if self.readonly:
            self.window.order_ticket_panel.update(
                {
                    "message": "Read-only mode is enabled. Disable it in settings before cancel requests.",
                    "level": "error",
                }
            )
            self.window.logs_panel.update({"message": "[WARN][execution] blocked cancel all: read-only mode enabled"})
            return

        confirm = QMessageBox.question(
            self.window,
            "Confirm Cancel All",
            "Cancel all open orders?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            self.window.order_ticket_panel.update({"message": "Cancel all aborted by user.", "level": "info"})
            self.window.logs_panel.update({"message": "[INFO][execution] cancel all aborted by user"})
            return

        self.window.order_ticket_panel.update({"message": "Cancel all queued for execution thread.", "level": "info"})
        self.window.logs_panel.update({"message": "[INFO][execution] cancel all queued"})
        self._order_worker.enqueue_cancel_all.emit({})

    # Render order worker responses in ticket/log panels.
    def _on_order_result(self, payload: Any) -> None:
        if self.window is None or not isinstance(payload, dict):
            return

        ok = bool(payload.get("ok", False))
        kind = str(payload.get("kind", "order")).strip().lower()
        message = str(payload.get("message", "Order response received."))
        if ok:
            level = "success" if kind in {"order", "cancel_all"} else "info"
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

    # Render fatal order worker errors.
    def _on_order_failed(self, message: str) -> None:
        if self.window is None:
            return
        self.window.order_ticket_panel.update({"message": f"Order worker failure: {message}", "level": "error"})
        self.window.logs_panel.update({"message": f"[ERROR][execution] order worker failure: {message}"})

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
            with self._io_lock:
                status = self.ib_client.get_status_snapshot()

        connection_state = str(status.get("connection_state", "")).lower()
        if not connection_state:
            with self._io_lock:
                connection_state = self.ib_client.get_connection_state(connecting=self._connecting)
        elif self._connecting and connection_state == "disconnected":
            connection_state = "connecting"

        connected = connection_state == "connected"
        pipeline_running = self._is_market_worker_running()
        order_thread_running = self._order_thread is not None and self._order_thread.isRunning()

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
        can_preview = connected and not self._connecting and order_thread_running
        can_place = can_preview and not self.readonly
        self.window.order_ticket_panel.preview_button.setEnabled(can_preview)
        self.window.order_ticket_panel.place_button.setEnabled(can_place)
        self.window.order_ticket_panel.cancel_all_button.setEnabled(can_place)

        if connected and (self._last_server_sync_sec is None or now_sec - self._last_server_sync_sec >= 10):
            self._start_server_time_sync()

    # Start connection flow using current settings.
    def _start_connect(self) -> None:
        if self._connecting or self.window is None:
            return
        with self._io_lock:
            if self.ib_client.is_connected():
                return

        self._stop_market_data_worker()
        with self._io_lock:
            self.ib_client.stop_live_streaming()

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
            with self._io_lock:
                connected = bool(self.ib_client.connect())
                if not connected:
                    error_message = self.ib_client.get_last_error_text() or "Unable to connect to IBKR."
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
            else:
                message = self._last_connect_error or "IB connection failed."
                self.window.logs_panel.update({"message": f"[ERROR][connection] {message}"})
                self.window.order_ticket_panel.update({"message": message, "level": "error"})
        self._refresh_status(force=True)

    # Release connect worker resources after completion.
    def _on_connect_finished(self) -> None:
        if self._connect_worker is not None:
            self._connect_worker.deleteLater()
        self._connect_worker = None

    # Start IB live market stream and polling worker.
    def _start_live_streaming(self) -> None:
        if self.window is None or self._is_market_worker_running():
            return

        with self._io_lock:
            connected = self.ib_client.is_connected()
        if not connected:
            self._refresh_status(force=True)
            return

        try:
            status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
            self._apply_status_settings(status_settings)
        except Exception as exc:
            self._last_connect_error = str(exc)
            print(f"Invalid streaming settings: {self._last_connect_error}")
            self._refresh_status(force=True)
            return

        with self._io_lock:
            started = self.ib_client.start_live_streaming(self.market_symbol)
        if not started:
            print("Live streaming start failed: could not subscribe market data.")
            self._refresh_status(force=True)
            return

        self._start_market_data_worker()
        self._refresh_status(force=True)
        if self.window is not None:
            self.window.logs_panel.update(
                {"message": f"[INFO][market_data] live stream started for {self.market_symbol}"}
            )

    # Stop live stream subscription and market polling worker.
    def _stop_live_streaming(self) -> None:
        self._stop_market_data_worker()
        with self._io_lock:
            self.ib_client.stop_live_streaming()
        self._refresh_status(force=True)
        if self.window is not None:
            self.window.logs_panel.update({"message": "[INFO][market_data] live stream stopped"})

    # Read status settings from UI controls (or current state fallback).
    def _read_status_settings_from_panel(self) -> dict[str, Any]:
        if self.window is None:
            return {
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
                "readonly": self.readonly,
                "market_symbol": self.market_symbol,
            }

        panel = self.window.status_panel
        return {
            "host": panel.host_input.text().strip(),
            "port": int(panel.port_input.value()),
            "client_id": int(panel.client_id_input.value()),
            "readonly": bool(panel.readonly_input.isChecked()),
            "market_symbol": panel.market_symbol_input.text().strip().upper(),
        }

    # Apply validated status settings to controller and IB client.
    def _apply_status_settings(self, settings: dict[str, Any]) -> None:
        self.host = str(settings["host"])
        self.port = int(settings["port"])
        self.client_id = int(settings["client_id"])
        self.readonly = bool(settings["readonly"])
        self.market_symbol = str(settings["market_symbol"]).upper()

        self.ib_client.host = self.host
        self.ib_client.port = self.port
        self.ib_client.client_id = self.client_id
        self.ib_client.readonly = self.readonly

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

        required = ("host", "port", "client_id", "readonly")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"Missing settings keys: {', '.join(missing)}")

        host = str(raw["host"]).strip()
        if not host:
            raise ValueError("Settings 'host' cannot be empty")

        symbol = str(raw.get("market_symbol", "EURUSD")).strip().upper()
        if not symbol:
            raise ValueError("Settings 'market_symbol' cannot be empty")

        return {
            "host": host,
            "port": int(raw["port"]),
            "client_id": int(raw["client_id"]),
            "readonly": bool(raw["readonly"]),
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
        return {
            "status": dict(Controller.DEFAULT_STATUS_SETTINGS),
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

    # Start background server-time synchronization if supported.
    def _start_server_time_sync(self) -> None:
        if self._server_time_worker is not None:
            try:
                if self._server_time_worker.isRunning():
                    return
            except RuntimeError:
                self._server_time_worker = None

        with self._io_lock:
            supports_server_time = self.ib_client.supports_server_time()
        if not supports_server_time:
            return

        self._server_time_worker = ServerTimeWorker(self.ib_client, self._io_lock)
        self._server_time_worker.result.connect(self._on_server_time_result)
        self._server_time_worker.finished.connect(self._on_server_time_finished)
        self._server_time_worker.start()

    # Store latest server time/latency snapshot and refresh UI.
    def _on_server_time_result(self, time_text: str, latency_text: str) -> None:
        self._server_time_text = time_text
        self._latency_ms_text = latency_text
        self._last_server_sync_sec = int(time.time())
        self._refresh_status(force=True)

    # Release server-time worker resources.
    def _on_server_time_finished(self) -> None:
        if self._server_time_worker is not None:
            self._server_time_worker.deleteLater()
        self._server_time_worker = None

    # Stop workers, subscriptions, and IB connection on exit.
    def _shutdown_services(self) -> None:
        if self._status_timer is not None:
            self._status_timer.stop()
        self._connecting = False

        if self._connect_worker is not None:
            try:
                if self._connect_worker.isRunning():
                    self._connect_worker.wait(1500)
            except RuntimeError:
                pass
            self._connect_worker = None

        self._stop_market_data_worker()
        self._stop_order_worker()

        with self._io_lock:
            self.ib_client.stop_live_streaming()
            self.ib_client.cancel_account_summary()
            if self.ib.isConnected():
                self.ib.disconnect()

    # Start the UI event loop and perform graceful shutdown.
    def run(self) -> int:
        self._create_window()
        self._setup_services()
        exit_code = self.app.exec_()
        self._shutdown_services()
        return exit_code
