import json
import sys
import time
from pathlib import Path
from threading import RLock

from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from ib_insync import IB

from services.ib_client import IBClient
from services.market_data_worker import MarketDataWorker
from services.order_worker import OrderWorker
from ui.main_window import MainWindow


class ServerTimeWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(str, str)

    def __init__(self, ib_client: IBClient, io_lock: RLock):
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock

    def run(self):
        with self.io_lock:
            time_text, latency_text = self.ib_client.get_server_time_and_latency()
        self.result.emit(time_text, latency_text)


class Controller:
    def __init__(self):
        self._settings_path = Path(__file__).resolve().parents[1] / "status_panel_settings.json"
        app_settings = self._load_app_settings()
        status_settings = app_settings["status"]

        self.host = status_settings["host"]
        self.port = status_settings["port"]
        self.client_id = status_settings["client_id"]
        self.readonly = status_settings["readonly"]
        self.market_symbol = status_settings["market_symbol"]

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

    def _create_window(self):
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

    def _setup_services(self):
        if self.window is None:
            return

        self._setup_order_worker()
        self._setup_market_data_worker()
        self.window.order_ticket_panel.place_button.clicked.connect(self._queue_order_from_ticket)
        self.window.order_ticket_panel.preview_button.clicked.connect(self._preview_order_from_ticket)

        self._status_timer = QTimer(self.window)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._refresh_status(force=True)

    def _setup_market_data_worker(self):
        if self.window is None or self._market_data_thread is not None:
            return
        thread = QtCore.QThread(self.window)
        worker = MarketDataWorker(
            ib_client=self.ib_client,
            io_lock=self._io_lock,
            interval_ms=100,
            snapshot_interval_ms=750,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        thread.finished.connect(worker.deleteLater)
        worker.payload_ready.connect(self._on_market_data_payload)
        worker.failed.connect(self._on_market_data_failed)
        self._market_data_thread = thread
        self._market_data_worker = worker

    def _setup_order_worker(self):
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

    def _is_market_worker_running(self) -> bool:
        return self._market_data_thread is not None and self._market_data_thread.isRunning()

    def _start_market_data_worker(self):
        self._setup_market_data_worker()
        if self._market_data_thread is None or self._market_data_thread.isRunning():
            return
        self._market_data_thread.start()

    def _stop_market_data_worker(self):
        thread = self._market_data_thread
        worker = self._market_data_worker
        if thread is None or not thread.isRunning():
            return

        if worker is not None:
            QtCore.QMetaObject.invokeMethod(worker, "stop", QtCore.Qt.BlockingQueuedConnection)
        thread.quit()
        thread.wait(1500)

    def _stop_order_worker(self):
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

    def _on_market_data_payload(self, payload):
        if self.window is None or not isinstance(payload, dict):
            return

        status_payload = payload.get("status")
        if isinstance(status_payload, dict):
            self._refresh_status(payload=status_payload)

        ticks = payload.get("ticks")
        if isinstance(ticks, list) and ticks:
            self.window.chart_panel.update({"ticks": ticks})
            self.window.logs_panel.update({"ticks": ticks})

        orders_payload = payload.get("orders_payload")
        if isinstance(orders_payload, dict):
            self.window.orders_panel.update(orders_payload)

        portfolio_payload = payload.get("portfolio_payload")
        if isinstance(portfolio_payload, dict):
            self.window.portfolio_panel.update(portfolio_payload)

    def _on_market_data_failed(self, message: str):
        if self.window is None:
            return
        self.window.logs_panel.update({"message": f"[WARN][market_data] worker error: {message}"})

    def _queue_order_from_ticket(self):
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

        symbol = request.get("symbol", "")
        side = request.get("side", "")
        qty = request.get("quantity", "")
        order_type = request.get("order_type", "")
        self.window.order_ticket_panel.update({"message": "Order queued for execution thread.", "level": "info"})
        self.window.logs_panel.update({"message": f"[INFO][execution] queued {side} {qty} {symbol} {order_type}"})
        self._order_worker.enqueue_order.emit(request)

    def _preview_order_from_ticket(self):
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

    def _on_order_result(self, payload):
        if self.window is None or not isinstance(payload, dict):
            return

        ok = bool(payload.get("ok", False))
        kind = str(payload.get("kind", "order")).strip().lower()
        message = str(payload.get("message", "Order response received."))
        if ok:
            level = "success" if kind == "order" else "info"
            log_level = "INFO"
        else:
            level = "error"
            log_level = "ERROR"
        source = "execution_preview" if kind == "preview" else "execution"

        self.window.order_ticket_panel.update({"message": message, "level": level})
        self.window.logs_panel.update({"message": f"[{log_level}][{source}] {message}"})

    def _on_order_failed(self, message: str):
        if self.window is None:
            return
        self.window.order_ticket_panel.update({"message": f"Order worker failure: {message}", "level": "error"})
        self.window.logs_panel.update({"message": f"[ERROR][execution] order worker failure: {message}"})

    def _refresh_status(self, payload: dict | None = None, force: bool = False):
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

        if connected and (self._last_server_sync_sec is None or now_sec - self._last_server_sync_sec >= 10):
            self._start_server_time_sync()

    def _start_connect(self):
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
        self._refresh_status(force=True)
        QApplication.processEvents()
        try:
            with self._io_lock:
                self.ib_client.connect()
        except Exception as exc:
            self._last_connect_error = str(exc)
            print(f"IB connection failed: {self._last_connect_error}")
        finally:
            self._connecting = False
            self._refresh_status(force=True)

    def _start_live_streaming(self):
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

    def _stop_live_streaming(self):
        self._stop_market_data_worker()
        with self._io_lock:
            self.ib_client.stop_live_streaming()
        self._refresh_status(force=True)
        if self.window is not None:
            self.window.logs_panel.update({"message": "[INFO][market_data] live stream stopped"})

    def _read_status_settings_from_panel(self) -> dict:
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

    def _apply_status_settings(self, settings: dict):
        self.host = str(settings["host"])
        self.port = int(settings["port"])
        self.client_id = int(settings["client_id"])
        self.readonly = bool(settings["readonly"])
        self.market_symbol = str(settings["market_symbol"]).upper()

        self.ib_client.host = self.host
        self.ib_client.port = self.port
        self.ib_client.client_id = self.client_id
        self.ib_client.readonly = self.readonly

    def _load_app_settings(self) -> dict:
        if not self._settings_path.exists():
            raise FileNotFoundError(f"Missing settings file: {self._settings_path}")
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except Exception:
            raise ValueError(f"Invalid settings JSON: {self._settings_path}")
        return self._validate_app_settings(raw)

    @staticmethod
    def _validate_app_settings(raw: dict) -> dict:
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

        return {"status": Controller._validate_status_settings(normalized_status)}

    @staticmethod
    def _validate_status_settings(raw: dict) -> dict:
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

    def _save_app_settings(self):
        status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
        self._apply_status_settings(status_settings)
        self._write_app_settings(status_settings)

    def _write_app_settings(self, status_settings: dict):
        app_settings = {"status": status_settings}
        try:
            self._settings_path.write_text(json.dumps(app_settings, indent=2), encoding="utf-8")
            print(f"Saved settings to {self._settings_path}")
        except Exception as exc:
            print(f"Failed to save settings: {exc}")

    def _start_server_time_sync(self):
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

    def _on_server_time_result(self, time_text: str, latency_text: str):
        self._server_time_text = time_text
        self._latency_ms_text = latency_text
        self._last_server_sync_sec = int(time.time())
        self._refresh_status(force=True)

    def _on_server_time_finished(self):
        if self._server_time_worker is not None:
            self._server_time_worker.deleteLater()
        self._server_time_worker = None

    def _shutdown_services(self):
        if self._status_timer is not None:
            self._status_timer.stop()

        self._stop_market_data_worker()
        self._stop_order_worker()

        with self._io_lock:
            self.ib_client.stop_live_streaming()
            self.ib_client.cancel_account_summary()
            if self.ib.isConnected():
                self.ib.disconnect()

    def run(self) -> int:
        self._create_window()
        self._setup_services()
        exit_code = self.app.exec_()
        self._shutdown_services()
        return exit_code
