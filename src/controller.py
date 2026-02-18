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
        settings = self._load_status_settings()

        self.host = settings["host"]
        self.port = settings["port"]
        self.client_id = settings["client_id"]
        self.readonly = settings["readonly"]
        self.max_candles = settings["max_candles"]
        self.market_symbol = settings["market_symbol"]

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
            max_candles=self.max_candles,
            on_connect=self._start_connect,
            on_start_live_streaming=self._start_live_streaming,
            on_save_settings=self._save_status_settings,
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            readonly=self.readonly,
            market_symbol=self.market_symbol,
        )
        self.window.resize(900, 600)
        self.window.show()

    def _setup_services(self):
        if self.window is None:
            return

        self._pipeline_runner = PipelineRunner(
            ib_client=self.ib_client,
            update_status=self._refresh_status,
            update_portfolio=self._update_portfolio_value,
            update_ticks=self._update_tick_series,
            interval_ms=100,
            parent=self.window,
        )

        self._status_timer = QTimer(self.window)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._refresh_status(force=True)

    def _refresh_status(self, force: bool = False):
        if self.window is None:
            return

        now_sec = int(time.time())
        if not force and self._last_status_sec == now_sec:
            return
        self._last_status_sec = now_sec

        status = self.ib_client.get_status_snapshot()
        connection_state = self.ib_client.get_connection_state(connecting=self._connecting)
        connected = connection_state == "connected"
        pipeline_running = self._pipeline_runner is not None and self._pipeline_runner.is_running()

        if not connected:
            self._latency_ms_text = "--"
            self._server_time_text = "--"

        self.window.status_panel.update(
            {
                "connection_state": connection_state,
                "mode": status["mode"],
                "env": status["env"],
                "client_id": status["client_id"],
                "account": status["account"],
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
            settings = self._validate_status_settings(self._read_status_settings_from_panel())
            self._apply_runtime_settings(settings)
        except Exception as exc:
            self._last_connect_error = str(exc)
            print(f"Invalid status panel settings: {self._last_connect_error}")
            self._refresh_status(force=True)
            return

        self.ib_client.ticker = None
        self.ib_client.last_bid = None
        self.ib_client.last_ask = None
        self.window.chart_panel.update({"max_candles": self.max_candles})

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
                "max_candles": self.max_candles,
                "market_symbol": self.market_symbol,
            }

        panel = self.window.status_panel
        return {
            "host": panel.host_input.text().strip(),
            "port": int(panel.port_input.value()),
            "client_id": int(panel.client_id_input.value()),
            "readonly": bool(panel.readonly_input.isChecked()),
            "max_candles": int(panel.max_candles_input.value()),
            "market_symbol": panel.market_symbol_input.text().strip().upper(),
        }

    def _apply_runtime_settings(self, settings: dict):
        self.host = str(settings["host"])
        self.port = int(settings["port"])
        self.client_id = int(settings["client_id"])
        self.readonly = bool(settings["readonly"])
        self.max_candles = int(settings["max_candles"])
        self.market_symbol = str(settings["market_symbol"]).upper()

        self.ib_client.host = self.host
        self.ib_client.port = self.port
        self.ib_client.client_id = self.client_id
        self.ib_client.readonly = self.readonly

    def _load_status_settings(self) -> dict:
        if not self._settings_path.exists():
            raise FileNotFoundError(f"Missing settings file: {self._settings_path}")
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except Exception:
            raise ValueError(f"Invalid settings JSON: {self._settings_path}")
        return self._validate_status_settings(raw)

    @staticmethod
    def _validate_status_settings(raw: dict) -> dict:
        if not isinstance(raw, dict):
            raise ValueError("Settings payload must be a JSON object")

        required = ("host", "port", "client_id", "readonly", "max_candles", "market_symbol")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"Missing settings keys: {', '.join(missing)}")

        host = str(raw["host"]).strip()
        symbol = str(raw["market_symbol"]).strip().upper()
        if not host:
            raise ValueError("Settings 'host' cannot be empty")
        if not symbol:
            raise ValueError("Settings 'market_symbol' cannot be empty")

        return {
            "host": host,
            "port": int(raw["port"]),
            "client_id": int(raw["client_id"]),
            "readonly": bool(raw["readonly"]),
            "max_candles": int(raw["max_candles"]),
            "market_symbol": symbol,
        }

    def _save_status_settings(self):
        settings = self._read_status_settings_from_panel()
        settings = self._validate_status_settings(settings)
        self._apply_runtime_settings(settings)
        if self.window is not None:
            self.window.chart_panel.update({"max_candles": self.max_candles})
        try:
            self._settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            print(f"Saved settings to {self._settings_path}")
        except Exception as exc:
            print(f"Failed to save settings: {exc}")

    def _update_portfolio_value(self):
        if self.window is None or not self.ib_client.is_connected():
            return
        summary, positions = self.ib_client.get_portfolio_snapshot()
        self.window.portfolio_panel.update({"summary": summary, "positions": positions})

    def _update_tick_series(self, bid: float, ask: float):
        if self.window is None:
            return
        self.window.chart_panel.update({"bid": bid, "ask": ask})

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
