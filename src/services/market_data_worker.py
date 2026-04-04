from __future__ import annotations

import time
from threading import RLock

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from services.ib_client import IBClient


class MarketDataWorker(QObject):
    payload_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        ib_client: IBClient,
        io_lock: RLock,
        interval_ms: int = 100,
        snapshot_interval_ms: int = 750,
    ):
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock
        self._interval_ms = max(25, int(interval_ms))
        self._snapshot_interval_ms = max(100, int(snapshot_interval_ms))
        self._timer: QTimer | None = None
        self._last_snapshot_monotonic = 0.0
        self._running = False

    @pyqtSlot()
    def start(self):
        if self._running:
            return
        self._running = True
        self._last_snapshot_monotonic = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._poll_once)
        self._timer.start()

    @pyqtSlot()
    def stop(self):
        self._running = False
        if self._timer is None:
            return
        self._timer.stop()
        self._timer.deleteLater()
        self._timer = None

    @pyqtSlot()
    def _poll_once(self):
        if not self._running:
            return

        try:
            with self.io_lock:
                status = self.ib_client.get_status_snapshot()
                connection_state = self.ib_client.get_connection_state()
                connected = connection_state == "connected"

                ticks = self.ib_client.process_messages() if connected else []
                if not isinstance(ticks, list):
                    ticks = []
                else:
                    ticks = [tick for tick in ticks if isinstance(tick, dict)]

                orders_payload = None
                portfolio_payload = None
                now = time.monotonic()
                if connected and (now - self._last_snapshot_monotonic) * 1000 >= self._snapshot_interval_ms:
                    open_orders = self.ib_client.get_open_orders_snapshot()
                    fills = self.ib_client.get_fills_snapshot()
                    summary, positions = self.ib_client.get_portfolio_snapshot()
                    orders_payload = {"open_orders": open_orders, "fills": fills}
                    portfolio_payload = {"summary": summary, "positions": positions}
                    self._last_snapshot_monotonic = now

            payload = {
                "status": {
                    "connection_state": connection_state,
                    "mode": status.get("mode", "--"),
                    "env": status.get("env", "--"),
                    "client_id": status.get("client_id", "--"),
                    "account": status.get("account", "--"),
                },
                "ticks": ticks,
                "orders_payload": orders_payload,
                "portfolio_payload": portfolio_payload,
            }
            self.payload_ready.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))
