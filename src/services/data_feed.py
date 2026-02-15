"""Data feed service that aggregates data for UI panels."""

from __future__ import annotations

import time
from typing import Any

from services.ib_client_bis import IBClient_bis


class DataFeed:
    def __init__(self, ib_client: IBClient_bis) -> None:
        self.ib_client = ib_client

        self.status_panel_data: dict[str, object] = {}
        self.portfolio_panel_data: dict[str, object] = {}
        self.chart_panel_data: dict[str, object] = {}
        self.orders_panel_data: dict[str, object] = {}
        self.performance_panel_data: dict[str, object] = {}
        self.risk_panel_data: dict[str, object] = {}
        self.logs_panel_data: dict[str, object] = {}

        self.last_snapshot: dict[str, dict[str, object]] = {}
        self.last_refresh_ts: float | None = None

    def request_all_panels(
        self,
        account: str = "",
        model_code: str = "",
        contract: Any = None,
        con_id: int | None = None,
        order: Any = None,
        preferred_account_tag: str = "NetLiquidation",
        chart_subscribe: bool = True,
        chart_wait_seconds: float = 0.0,
        auto_cancel_performance_pnl: bool = False,
        auto_cancel_risk_pnl: bool = False,
    ) -> dict[str, dict[str, object]]:
        """
        Request all 7 non-robot panel payloads and store them on this object.
        """
        connected = bool(self.ib_client.ib.isConnected())
        self.ib_client.is_connected = connected
        if not connected:
            self.status_panel_data = {
                "ib_connected": False,
                "account": "--",
                "server_time_text": "--",
                "latency_text": "--",
                "environment": self.ib_client.get_environment(),
            }
            self.portfolio_panel_data = {}
            self.chart_panel_data = {}
            self.orders_panel_data = {}
            self.performance_panel_data = {}
            self.risk_panel_data = {}
            self.logs_panel_data = {}
            self.last_snapshot = {
                "status": self.status_panel_data,
                "portfolio": self.portfolio_panel_data,
                "chart": self.chart_panel_data,
                "orders": self.orders_panel_data,
                "performance": self.performance_panel_data,
                "risk": self.risk_panel_data,
                "logs": self.logs_panel_data,
            }
            self.last_refresh_ts = time.time()
            return self.last_snapshot

        self.status_panel_data = self.ib_client.get_status_panel_variables_from_ib()
        self.portfolio_panel_data = self.ib_client.get_portfolio_panel_variables_from_ib(
            account=account
        )
        self.chart_panel_data = self.ib_client.get_chart_panel_variables_from_ib(
            contract=contract,
            subscribe=chart_subscribe,
            wait_seconds=chart_wait_seconds,
        )
        self.orders_panel_data = self.ib_client.get_orders_panel_variables_from_ib()
        self.performance_panel_data = self.ib_client.get_performance_panel_variables_from_ib(
            account=account,
            model_code=model_code,
            con_id=con_id,
            auto_cancel=auto_cancel_performance_pnl,
        )
        self.risk_panel_data = self.ib_client.get_risk_panel_variables_from_ib(
            account=account,
            model_code=model_code,
            contract=contract,
            order=order,
            preferred_account_tag=preferred_account_tag,
            auto_cancel_pnl=auto_cancel_risk_pnl,
        )
        self.logs_panel_data = self.ib_client.get_logs_panel_variables_from_ib()

        self.last_snapshot = {
            "status": self.status_panel_data,
            "portfolio": self.portfolio_panel_data,
            "chart": self.chart_panel_data,
            "orders": self.orders_panel_data,
            "performance": self.performance_panel_data,
            "risk": self.risk_panel_data,
            "logs": self.logs_panel_data,
        }
        self.last_refresh_ts = time.time()
        return self.last_snapshot
