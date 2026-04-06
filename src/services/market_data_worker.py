from __future__ import annotations

import time
from threading import RLock

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from services.ib_client import IBClient


class MarketDataWorker(QObject):
    NO_TICK_CHECK_SECONDS = 2.0
    NO_TICK_CHECK_REPETITIONS = 3

    payload_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    # Initialize periodic market-data polling state and timers.
    def __init__(
        self,
        ib_client: IBClient,
        io_lock: RLock,
        interval_ms: int = 100,
        snapshot_interval_ms: int = 750,
    ) -> None:
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock
        self._interval_ms = max(25, int(interval_ms))
        self._snapshot_interval_ms = max(100, int(snapshot_interval_ms))
        self._timer: QTimer | None = None
        self._last_snapshot_monotonic = 0.0
        self._running = False
        self._no_tick_check_started_at: float | None = None
        self._no_tick_check_count = 0
        self._no_tick_warning_emitted = False

    @pyqtSlot()
    # Start periodic polling on the worker thread.
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._last_snapshot_monotonic = 0.0
        self._no_tick_check_started_at = None
        self._no_tick_check_count = 0
        self._no_tick_warning_emitted = False
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._poll_once)
        self._timer.start()

    @pyqtSlot()
    # Stop polling and release the QTimer owned by this worker.
    def stop(self) -> None:
        self._running = False
        if self._timer is None:
            return
        self._timer.stop()
        self._timer.deleteLater()
        self._timer = None

    @pyqtSlot()
    # Poll IB state once and emit a normalized payload for the UI.
    def _poll_once(self) -> None:
        if not self._running:
            return

        try:
            messages: list[str] = []
            with self.io_lock:
                status = self.ib_client.get_status_snapshot()
                connection_state = self.ib_client.get_connection_state()
                connected = connection_state == "connected"
                now = time.monotonic()

                ticks = self.ib_client.process_messages() if connected else []
                if not isinstance(ticks, list):
                    ticks = []
                else:
                    ticks = [tick for tick in ticks if isinstance(tick, dict)]

                if connected:
                    if ticks:
                        if self._no_tick_warning_emitted:
                            messages.append("[INFO][market_data] tick stream resumed.")
                        self._no_tick_check_started_at = None
                        self._no_tick_check_count = 0
                        self._no_tick_warning_emitted = False
                    else:
                        if self._no_tick_check_started_at is None:
                            self._no_tick_check_started_at = now
                        elif not self._no_tick_warning_emitted:
                            no_tick_seconds = now - self._no_tick_check_started_at
                            if no_tick_seconds >= self.NO_TICK_CHECK_SECONDS:
                                self._no_tick_check_count += 1
                                self._no_tick_check_started_at = now
                                check_position = self._no_tick_check_count
                                if check_position >= self.NO_TICK_CHECK_REPETITIONS:
                                    messages.append(
                                        f"[WARN][market_data] no ticks received "
                                        f"(test {self.NO_TICK_CHECK_REPETITIONS}/{self.NO_TICK_CHECK_REPETITIONS}); "
                                        "market may be closed or data is unavailable for this symbol."
                                    )
                                    self._no_tick_warning_emitted = True
                                else:
                                    messages.append(
                                        f"[INFO][market_data] no ticks received "
                                        f"(test {check_position}/{self.NO_TICK_CHECK_REPETITIONS})."
                                    )
                else:
                    self._no_tick_check_started_at = None
                    self._no_tick_check_count = 0
                    self._no_tick_warning_emitted = False

                orders_payload = None
                portfolio_payload = None
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
                "messages": messages,
            }
            self.payload_ready.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))
