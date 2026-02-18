from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import QObject, QTimer

from services.ib_client import IBClient


class PipelineRunner(QObject):
    """
    Periodic runner for the live UI pipeline.

    It drives the periodic live data loop from the controller:
    - update status
    - process IB messages
    - update portfolio
    - fetch latest bid/ask
    - push tick update callback
    """

    def __init__(
        self,
        ib_client: IBClient,
        update_status_panel: Callable[[dict], None],
        chart_panel,
        portfolio_panel,
        orders_panel,
        performance_panel,
        risk_panel,
        robots_panel,
        logs_panel,
        interval_ms: int = 100,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.ib_client = ib_client
        self._update_status_panel = update_status_panel
        self.chart_panel = chart_panel
        self.portfolio_panel = portfolio_panel
        self.orders_panel = orders_panel
        self.performance_panel = performance_panel
        self.risk_panel = risk_panel
        self.robots_panel = robots_panel
        self.logs_panel = logs_panel

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

    def get_status_panel_payload(self) -> dict:
        status = self.ib_client.get_status_snapshot()
        return {
            "connection_state": self.ib_client.get_connection_state(),
            "mode": status.get("mode", "--"),
            "env": status.get("env", "--"),
            "client_id": status.get("client_id", "--"),
            "account": status.get("account", "--"),
        }

    def get_chart_panel_payload(self) -> dict | None:
        bid, ask = self.ib_client.get_latest_bid_ask()
        if bid is None or ask is None:
            return None
        return {"bid": bid, "ask": ask}

    def get_portfolio_panel_payload(self) -> dict:
        summary, positions = self.ib_client.get_portfolio_snapshot()
        return {"summary": summary, "positions": positions}

    def get_orders_panel_payload(self) -> dict:
        return {
            "open_orders": self.ib_client.get_open_orders_snapshot(),
            "fills": self.ib_client.get_fills_snapshot(),
        }

    def get_performance_panel_payload(self) -> dict | None:
        return None

    def get_risk_panel_payload(self) -> dict | None:
        return None

    def get_robots_panel_payload(self) -> dict | None:
        return None

    def get_logs_panel_payload(self) -> dict | None:
        return None

    def update_chart_panel(self, payload: dict):
        if self.chart_panel is None:
            return
        self.chart_panel.update(payload)

    def update_portfolio_panel(self, payload: dict):
        if self.portfolio_panel is None:
            return
        self.portfolio_panel.update(payload)

    def update_orders_panel(self, payload: dict):
        if self.orders_panel is None:
            return
        self.orders_panel.update(payload)

    def update_performance_panel(self, payload: dict):
        if self.performance_panel is None:
            return
        self.performance_panel.update(payload)

    def update_risk_panel(self, payload: dict):
        if self.risk_panel is None:
            return
        self.risk_panel.update(payload)

    def update_robots_panel(self, payload: dict):
        if self.robots_panel is None:
            return
        self.robots_panel.update(payload)

    def update_logs_panel(self, payload: dict):
        if self.logs_panel is None:
            return
        self.logs_panel.update(payload)

    def run_once(self):
        connected = self.ib_client.is_connected()
        self._update_status_panel(self.get_status_panel_payload())

        performance_payload = self.get_performance_panel_payload()
        if performance_payload is not None:
            self.update_performance_panel(performance_payload)

        risk_payload = self.get_risk_panel_payload()
        if risk_payload is not None:
            self.update_risk_panel(risk_payload)

        robots_payload = self.get_robots_panel_payload()
        if robots_payload is not None:
            self.update_robots_panel(robots_payload)

        logs_payload = self.get_logs_panel_payload()
        if logs_payload is not None:
            self.update_logs_panel(logs_payload)
        self.update_orders_panel(self.get_orders_panel_payload())

        if not connected:
            self.update_portfolio_panel(self.get_portfolio_panel_payload())
            return

        self.ib_client.process_messages()
        self.update_portfolio_panel(self.get_portfolio_panel_payload())

        chart_payload = self.get_chart_panel_payload()
        if chart_payload is None:
            return

        self.update_chart_panel(chart_payload)
