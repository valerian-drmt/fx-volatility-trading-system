from __future__ import annotations

import time

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from services.ib_client import IBClient


class SnapshotPayloadWorker(QObject):
    payload_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, ib_client: IBClient):
        super().__init__()
        self.ib_client = ib_client

    @pyqtSlot()
    def collect(self):
        try:
            orders_payload = {
                "open_orders": self.ib_client.get_open_orders_snapshot(),
                "fills": self.ib_client.get_fills_snapshot(),
            }
            summary, positions = self.ib_client.get_portfolio_snapshot()
            portfolio_payload = {"summary": summary, "positions": positions}
            payload = {
                "orders_payload": orders_payload,
                "portfolio_payload": portfolio_payload,
                "performance_payload": None,
                "risk_payload": None,
                "robots_payload": None,
            }
            self.payload_ready.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))


class SnapshotThreadLoop(QObject):
    request_payload = pyqtSignal()
    payload_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, ib_client: IBClient, interval_ms: int = 750, parent: QObject | None = None):
        super().__init__(parent)
        self.ib_client = ib_client
        self._interval_ms = max(100, int(interval_ms))
        self._last_request_at = 0.0
        self._in_flight = False
        self._worker_enabled = True
        self._worker_thread: QThread | None = None
        self._worker: SnapshotPayloadWorker | None = None

    def start(self):
        self._worker_enabled = True
        self._last_request_at = 0.0
        self._in_flight = False
        self._ensure_worker()

    def stop(self):
        self._in_flight = False
        self._stop_worker()

    def set_interval(self, interval_ms: int):
        self._interval_ms = max(100, int(interval_ms))

    def reset_in_flight(self):
        self._in_flight = False

    def request_if_due(self):
        if self._in_flight:
            return

        now = time.monotonic()
        if (now - self._last_request_at) * 1000 < self._interval_ms:
            return
        self._last_request_at = now
        self._in_flight = True

        if self._worker_enabled and self._worker_thread is not None and self._worker_thread.isRunning():
            self.request_payload.emit()
            return

        try:
            payload = self._collect_snapshot_payload_sync()
            self._on_worker_payload_ready(payload)
        except Exception as exc:
            self._on_worker_failed(str(exc))

    def _collect_snapshot_payload_sync(self) -> dict:
        orders_payload = {
            "open_orders": self.ib_client.get_open_orders_snapshot(),
            "fills": self.ib_client.get_fills_snapshot(),
        }
        summary, positions = self.ib_client.get_portfolio_snapshot()
        portfolio_payload = {"summary": summary, "positions": positions}
        return {
            "orders_payload": orders_payload,
            "portfolio_payload": portfolio_payload,
            "performance_payload": None,
            "risk_payload": None,
            "robots_payload": None,
        }

    def _ensure_worker(self):
        if not self._worker_enabled:
            return
        if self._worker_thread is not None:
            return

        thread = QThread(self)
        worker = SnapshotPayloadWorker(self.ib_client)
        worker.moveToThread(thread)
        self.request_payload.connect(worker.collect)
        worker.payload_ready.connect(self._on_worker_payload_ready)
        worker.failed.connect(self._on_worker_failed)
        thread.finished.connect(worker.deleteLater)
        thread.start()

        self._worker_thread = thread
        self._worker = worker

    def _stop_worker(self):
        self._worker = None
        thread = self._worker_thread
        self._worker_thread = None
        if thread is None:
            return
        thread.quit()
        thread.wait(1000)
        thread.deleteLater()

    @pyqtSlot(object)
    def _on_worker_payload_ready(self, payload):
        self._in_flight = False
        self.payload_ready.emit(payload)

    @pyqtSlot(str)
    def _on_worker_failed(self, message: str):
        self._in_flight = False
        if self._worker_enabled:
            self._worker_enabled = False
            self._stop_worker()
        self.failed.emit(message)
