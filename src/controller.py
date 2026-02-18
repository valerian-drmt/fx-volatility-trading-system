import json
import sys
import time
from pathlib import Path

from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from ib_insync import IB

from services.ib_client import IBClient
from services.pipeline_runner import PipelineRunner
from ui.main_window import MainWindow


class ServerTimeWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(str, str)

    def __init__(self, ib_client: IBClient):
        super().__init__()
        self.ib_client = ib_client

    def run(self):
        time_text, latency_text = self.ib_client.get_server_time_and_latency()
        self.result.emit(time_text, latency_text)


class Controller:
    def __init__(self):
        self._settings_path = Path(__file__).resolve().parents[1] / "status_panel_settings.json"
        app_settings = self._load_app_settings()
        status_settings = app_settings["status"]
        live_streaming_settings = app_settings["live_streaming"]

        self.host = status_settings["host"]
        self.port = status_settings["port"]
        self.client_id = status_settings["client_id"]
        self.readonly = status_settings["readonly"]
        self.max_candles = live_streaming_settings["max_candles"]
        self.market_symbol = live_streaming_settings["market_symbol"]

        self.app = QApplication(sys.argv)
        self.ib = IB()
        self.ib_client = IBClient(
            ib=self.ib,
            ticker=None,
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            readonly=self.readonly,
        )

        self.window: MainWindow | None = None
        self._pipeline_runner: PipelineRunner | None = None
        self._status_timer: QTimer | None = None
        self._server_time_worker: ServerTimeWorker | None = None

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
            on_save_settings=self._save_app_settings,
            on_save_live_streaming_settings=self._save_live_streaming_settings,
            status_defaults={
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
                "readonly": self.readonly,
            },
            live_streaming_defaults={
                "max_candles": self.max_candles,
                "market_symbol": self.market_symbol,
            },
        )
        self.window.resize(900, 600)
        self.window.show()

    def _setup_services(self):
        if self.window is None:
            return

        self._pipeline_runner = PipelineRunner(
            ib_client=self.ib_client,
            update_status_panel=self._refresh_status,
            chart_panel=self.window.chart_panel,
            portfolio_panel=self.window.portfolio_panel,
            orders_panel=self.window.orders_panel,
            performance_panel=self.window.performance_panel,
            risk_panel=self.window.risk_panel,
            robots_panel=self.window.robots_panel,
            logs_panel=self.window.logs_panel,
            interval_ms=100,
            parent=self.window,
        )

        self._status_timer = QTimer(self.window)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._refresh_status(force=True)

    def _refresh_status(self, payload: dict | None = None, force: bool = False):
        if self.window is None:
            return

        now_sec = int(time.time())
        if not force and self._last_status_sec == now_sec:
            return
        self._last_status_sec = now_sec

        status = payload if isinstance(payload, dict) else self.ib_client.get_status_snapshot()
        connection_state = str(status.get("connection_state", "")).lower()
        if not connection_state:
            connection_state = self.ib_client.get_connection_state(connecting=self._connecting)
        elif self._connecting and connection_state == "disconnected":
            connection_state = "connecting"

        connected = connection_state == "connected"
        pipeline_running = self._pipeline_runner is not None and self._pipeline_runner.is_running()

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
        if self._connecting or self.ib_client.is_connected() or self.window is None:
            return

        try:
            status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
            live_streaming_settings = self._validate_live_streaming_settings(
                self._read_live_streaming_settings_from_panel()
            )
            self._apply_status_settings(status_settings)
            self._apply_live_streaming_settings(live_streaming_settings)
        except Exception as exc:
            self._last_connect_error = str(exc)
            print(f"Invalid settings: {self._last_connect_error}")
            self._refresh_status(force=True)
            return

        self.ib_client.ticker = None
        self.ib_client.last_bid = None
        self.ib_client.last_ask = None
        self.window.chart_panel.update({"max_candles": self.max_candles, "market_symbol": self.market_symbol})

        self._connecting = True
        self._refresh_status(force=True)
        QApplication.processEvents()
        try:
            self.ib_client.connect_and_prepare(self.market_symbol)
        except Exception as exc:
            self._last_connect_error = str(exc)
            print(f"IB connection failed: {self._last_connect_error}")
        finally:
            self._connecting = False
            self._refresh_status(force=True)

    def _start_live_streaming(self):
        if self._pipeline_runner is None or self._pipeline_runner.is_running():
            return
        if not self.ib_client.is_connected():
            self._refresh_status(force=True)
            return

        self._pipeline_runner.start()
        self._refresh_status(force=True)

    def _read_status_settings_from_panel(self) -> dict:
        if self.window is None:
            return {
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
                "readonly": self.readonly,
            }

        panel = self.window.status_panel
        return {
            "host": panel.host_input.text().strip(),
            "port": int(panel.port_input.value()),
            "client_id": int(panel.client_id_input.value()),
            "readonly": bool(panel.readonly_input.isChecked()),
        }

    def _read_live_streaming_settings_from_panel(self) -> dict:
        if self.window is None:
            return {
                "max_candles": self.max_candles,
                "market_symbol": self.market_symbol,
            }

        panel = self.window.chart_panel
        return {
            "max_candles": int(panel.max_candles_input.value()),
            "market_symbol": panel.market_symbol_input.text().strip().upper(),
        }

    def _apply_status_settings(self, settings: dict):
        self.host = str(settings["host"])
        self.port = int(settings["port"])
        self.client_id = int(settings["client_id"])
        self.readonly = bool(settings["readonly"])

        self.ib_client.host = self.host
        self.ib_client.port = self.port
        self.ib_client.client_id = self.client_id
        self.ib_client.readonly = self.readonly

    def _apply_live_streaming_settings(self, settings: dict):
        self.max_candles = int(settings["max_candles"])
        self.market_symbol = str(settings["market_symbol"]).upper()

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

        required_sections = ("status", "live_streaming")
        missing_sections = [key for key in required_sections if key not in raw]
        if missing_sections:
            raise ValueError(f"Missing settings sections: {', '.join(missing_sections)}")

        return {
            "status": Controller._validate_status_settings(raw["status"]),
            "live_streaming": Controller._validate_live_streaming_settings(raw["live_streaming"]),
        }

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

        return {
            "host": host,
            "port": int(raw["port"]),
            "client_id": int(raw["client_id"]),
            "readonly": bool(raw["readonly"]),
        }

    @staticmethod
    def _validate_live_streaming_settings(raw: dict) -> dict:
        if not isinstance(raw, dict):
            raise ValueError("Live streaming settings payload must be a JSON object")

        required = ("max_candles", "market_symbol")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"Missing live streaming settings keys: {', '.join(missing)}")

        symbol = str(raw["market_symbol"]).strip().upper()
        if not symbol:
            raise ValueError("Settings 'market_symbol' cannot be empty")

        max_candles = int(raw["max_candles"])
        if max_candles <= 0:
            raise ValueError("Settings 'max_candles' must be > 0")

        return {
            "max_candles": max_candles,
            "market_symbol": symbol,
        }

    def _save_app_settings(self):
        status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
        live_streaming_settings = self._validate_live_streaming_settings(
            self._read_live_streaming_settings_from_panel()
        )
        self._apply_status_settings(status_settings)
        self._apply_live_streaming_settings(live_streaming_settings)
        if self.window is not None:
            self.window.chart_panel.update(
                {"max_candles": self.max_candles, "market_symbol": self.market_symbol}
            )
        self._write_app_settings(status_settings, live_streaming_settings)

    def _save_live_streaming_settings(self):
        live_streaming_settings = self._validate_live_streaming_settings(
            self._read_live_streaming_settings_from_panel()
        )
        self._apply_live_streaming_settings(live_streaming_settings)
        if self.window is not None:
            self.window.chart_panel.update(
                {"max_candles": self.max_candles, "market_symbol": self.market_symbol}
            )
        status_settings = {
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
            "readonly": self.readonly,
        }
        self._write_app_settings(status_settings, live_streaming_settings)

    def _write_app_settings(self, status_settings: dict, live_streaming_settings: dict):
        app_settings = {"status": status_settings, "live_streaming": live_streaming_settings}
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

        if not self.ib_client.supports_server_time():
            return

        self._server_time_worker = ServerTimeWorker(self.ib_client)
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

        if self._pipeline_runner is not None:
            self._pipeline_runner.stop()

        self.ib_client.cancel_account_summary()
        if self.ib.isConnected():
            self.ib.disconnect()

    def run(self) -> int:
        self._create_window()
        self._setup_services()
        exit_code = self.app.exec_()
        self._shutdown_services()
        return exit_code
