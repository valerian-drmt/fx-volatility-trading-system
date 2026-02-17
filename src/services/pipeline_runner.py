from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import QObject, QTimer

from services.ib_client import IBClient


class PipelineRunner(QObject):
    """
    Periodic runner for the live UI pipeline.

    It drives the same sequence previously in LiveTickWindow.update_data_and_plot:
    - update status
    - process IB messages
    - update portfolio
    - fetch latest bid/ask
    - push tick update callback
    """

    def __init__(
        self,
        ib_client: IBClient,
        update_status: Callable[[], None],
        update_portfolio: Callable[[], None],
        update_ticks: Callable[[float, float], None],
        interval_ms: int = 100,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.ib_client = ib_client
        self.update_status = update_status
        self.update_portfolio = update_portfolio
        self.update_ticks = update_ticks

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.run_once)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def set_interval(self, interval_ms: int):
        self._timer.setInterval(interval_ms)

    def run_once(self):
        connected = self.ib_client.is_connected()
        self.update_status()
        if not connected:
            return

        self.ib_client.process_messages()
        self.update_portfolio()

        bid, ask = self.ib_client.get_latest_bid_ask()
        if bid is None or ask is None:
            return

        self.update_ticks(bid, ask)
