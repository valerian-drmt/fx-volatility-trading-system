from __future__ import annotations

import math
from typing import Callable

from PyQt5.QtCore import QObject, QTimer

from services.ib_client import IBClient
from services.pipeline_snapshot_thread import SnapshotThreadLoop


class PipelineRunner(QObject):
    """
    Periodic runner for the live UI pipeline.

    It drives the periodic live data loop from the controller:
    - fast tick loop (status + ticks + logs + robot manager)
    - slow chart loop (chart repaint cadence)
    - collect slow snapshots asynchronously
    - update slow panels from snapshot cache
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
        chart_interval_ms: int = 1000,
        snapshot_interval_ms: int = 750,
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
        self._snapshot_payload_pending = False
        self._chart_interval_ms = max(100, int(chart_interval_ms))
        self._current_candle: dict | None = None
        self._next_candle_index = 1
        self._last_valid_tick_price: float | None = None

        self._snapshot_loop = SnapshotThreadLoop(
            ib_client=self.ib_client,
            interval_ms=snapshot_interval_ms,
            parent=self,
        )
        self._snapshot_loop.payload_ready.connect(self._on_snapshot_payload_ready)
        self._snapshot_loop.failed.connect(self._on_snapshot_payload_failed)

        self._orders_payload_cache = {"open_orders": [], "fills": []}
        self._portfolio_payload_cache = {"summary": [], "positions": []}
        self._performance_payload_cache = None
        self._risk_payload_cache = None
        self._robots_payload_cache = None

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(interval_ms)
        self._tick_timer.timeout.connect(self.qtimer_tick_loop)

        self._chart_timer = QTimer(self)
        self._chart_timer.setInterval(self._chart_interval_ms)
        self._chart_timer.timeout.connect(self.qtimer_chart_loop)

    def start(self):
        self._snapshot_loop.start()
        self._snapshot_payload_pending = True
        self._current_candle = None
        self._next_candle_index = 1
        self._last_valid_tick_price = None
        self._tick_timer.start()
        self._chart_timer.start()

    def stop(self):
        self._tick_timer.stop()
        self._chart_timer.stop()
        self._snapshot_loop.stop()
        self._current_candle = None
        self._last_valid_tick_price = None

    def is_running(self) -> bool:
        return self._tick_timer.isActive() or self._chart_timer.isActive()

    def set_interval(self, interval_ms: int):
        self._tick_timer.setInterval(interval_ms)

    def set_chart_interval(self, interval_ms: int):
        self._chart_interval_ms = max(100, int(interval_ms))
        self._chart_timer.setInterval(self._chart_interval_ms)

    def set_snapshot_interval(self, interval_ms: int):
        self._snapshot_loop.set_interval(interval_ms)

    def get_status_panel_payload(self) -> dict:
        status = self.ib_client.get_status_snapshot()
        return {
            "connection_state": self.ib_client.get_connection_state(),
            "mode": status.get("mode", "--"),
            "env": status.get("env", "--"),
            "client_id": status.get("client_id", "--"),
            "account": status.get("account", "--"),
        }

    @staticmethod
    def _is_valid_tick_price(value) -> bool:
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return not math.isnan(value)
        return False

    def _get_tick_price(self, tick: dict) -> float | None:
        bid = tick.get("bid")
        ask = tick.get("ask")
        last = tick.get("last")

        candidate = None
        has_bid = self._is_valid_tick_price(bid)
        has_ask = self._is_valid_tick_price(ask)
        if has_bid and has_ask:
            candidate = (float(bid) + float(ask)) / 2.0
        elif has_bid:
            candidate = float(bid)
        elif has_ask:
            candidate = float(ask)
        elif self._is_valid_tick_price(last):
            candidate = float(last)

        if candidate is None:
            return None
        # Keep native precision from source, and reject suspicious spikes/spreads.
        reference = self._last_valid_tick_price if self._last_valid_tick_price is not None else candidate

        if has_bid and has_ask:
            spread = abs(float(ask) - float(bid))
            spread_limit = max(0.0015, abs(reference) * 0.0012)
            if spread > spread_limit:
                return None

        if self._last_valid_tick_price is not None:
            jump = abs(candidate - self._last_valid_tick_price)
            jump_limit = max(0.0040, abs(self._last_valid_tick_price) * 0.0030)
            if jump > jump_limit:
                return None

        self._last_valid_tick_price = candidate
        return candidate

    def _ingest_ticks_into_current_candle(self, ticks: list[dict]):
        for tick in ticks:
            if not isinstance(tick, dict):
                continue

            price = self._get_tick_price(tick)
            if price is None:
                continue

            if self._current_candle is None:
                self._current_candle = {
                    "index": self._next_candle_index,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
                continue

            self._current_candle["high"] = max(self._current_candle["high"], price)
            self._current_candle["low"] = min(self._current_candle["low"], price)
            self._current_candle["close"] = price

    def get_ib_ticks(self) -> list[dict]:
        ticks = self.ib_client.process_messages()
        if not isinstance(ticks, list):
            return []
        return [tick for tick in ticks if isinstance(tick, dict)]

    def get_logs_panel_payload(self, ticks: list[dict]) -> dict | None:
        if not ticks:
            return None
        return {"ticks": ticks}

    def _on_snapshot_payload_ready(self, payload):
        if not isinstance(payload, dict):
            return

        orders_payload = payload.get("orders_payload")
        portfolio_payload = payload.get("portfolio_payload")

        if isinstance(orders_payload, dict):
            self._orders_payload_cache = orders_payload
        if isinstance(portfolio_payload, dict):
            self._portfolio_payload_cache = portfolio_payload

        self._performance_payload_cache = payload.get("performance_payload")
        self._risk_payload_cache = payload.get("risk_payload")
        self._robots_payload_cache = payload.get("robots_payload")
        self._snapshot_payload_pending = True

    def _on_snapshot_payload_failed(self, message: str):
        self.update_logs_panel(
            {"message": f"[WARN][pipeline] Snapshot worker disabled, fallback sync mode: {message}"}
        )

    def update_chart_panel(self, payload: dict):
        if self.chart_panel is None:
            return
        self.chart_panel.update(payload)

    def update_status_panel(self, payload: dict):
        if not callable(self._update_status_panel):
            return
        self._update_status_panel(payload)

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

    def update_robot_manager(
        self,
        ticks: list[dict],
        risk_payload: dict | None,
        orders_payload: dict,
        performance_payload: dict | None,
        portfolio_payload: dict,
        robots_payload: dict | None,
    ):
        pass

    def qtimer_tick_loop(self):
        # 1) Status Update
        status_payload = self.get_status_panel_payload()
        self.update_status_panel(status_payload)
        connected = status_payload.get("connection_state") == "connected"

        # 2) Connection Gate
        if not connected:
            self._current_candle = None
            self._last_valid_tick_price = None
            self._snapshot_loop.reset_in_flight()
            return

        # 3) Collect fast live-stream ticks.
        tick_payload = self.get_ib_ticks()

        # 4) Collect slow snapshots on dedicated cadence/thread.
        self._snapshot_loop.request_if_due()
        risk_payload = self._risk_payload_cache
        orders_payload = self._orders_payload_cache
        performance_payload = self._performance_payload_cache
        portfolio_payload = self._portfolio_payload_cache
        robots_payload = self._robots_payload_cache

        # 5) Forward payloads to robot manager pipeline block.
        self.update_robot_manager(
            ticks=tick_payload,
            risk_payload=risk_payload,
            orders_payload=orders_payload,
            performance_payload=performance_payload,
            portfolio_payload=portfolio_payload,
            robots_payload=robots_payload,
        )

        # 6) Update slow UI panels only when new snapshot payload is available.
        if self._snapshot_payload_pending:
            if performance_payload is not None:
                self.update_performance_panel(performance_payload)
            if risk_payload is not None:
                self.update_risk_panel(risk_payload)
            if robots_payload is not None:
                self.update_robots_panel(robots_payload)
            self.update_orders_panel(orders_payload)
            self.update_portfolio_panel(portfolio_payload)
            self._snapshot_payload_pending = False

        # 7) Log Update
        logs_payload = self.get_logs_panel_payload(tick_payload)
        if logs_payload is not None:
            self.update_logs_panel(logs_payload)

        # 8) Build current 1-second candle from incoming ticks.
        self._ingest_ticks_into_current_candle(tick_payload)

    def qtimer_chart_loop(self):
        if self._current_candle is None:
            return
        candle_payload = {"candle": dict(self._current_candle)}
        self._current_candle = None
        self._next_candle_index += 1
        self.update_chart_panel(candle_payload)

    # Backward-compat alias (existing diagrams/docs may still reference this name).
    def qtimer_loop(self):
        self.qtimer_tick_loop()
