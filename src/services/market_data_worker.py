from __future__ import annotations

import time

from services.ib_client import IBClient


class MarketDataWorker:
    NO_TICK_CHECK_SECONDS = 2.0
    NO_TICK_CHECK_REPETITIONS = 3

    # Initialize market-data polling state.
    def __init__(
        self,
        ib_client: IBClient,
        interval_ms: int = 100,
        snapshot_interval_ms: int = 750,
        orders_client: IBClient | None = None,
        portfolio_client: IBClient | None = None,
        status_client: IBClient | None = None,
    ) -> None:
        self.ib_client = ib_client
        self._orders_client = orders_client if orders_client is not None else ib_client
        self._portfolio_client = portfolio_client if portfolio_client is not None else ib_client
        self._status_client = status_client if status_client is not None else ib_client
        self._interval_ms = max(25, int(interval_ms))
        self._snapshot_interval_ms = max(100, int(snapshot_interval_ms))
        self._last_snapshot_monotonic = 0.0
        self._no_tick_check_started_at: float | None = None
        self._no_tick_check_count = 0
        self._no_tick_warning_emitted = False
        self._has_received_stream_ticks = False

    # Collect open-orders from cached snapshot (no active IB request).
    @staticmethod
    def _collect_open_orders(orders_client: IBClient) -> list[object]:
        try:
            open_orders = orders_client.get_open_orders_snapshot() or []
        except Exception:
            open_orders = []
        return open_orders if isinstance(open_orders, list) else []

    # Collect recent fills from cached snapshot (no active IB request).
    @staticmethod
    def _collect_recent_orders(orders_client: IBClient) -> list[object]:
        try:
            recent_orders = orders_client.get_fills_snapshot() or []
        except Exception:
            recent_orders = []
        return recent_orders if isinstance(recent_orders, list) else []

    # Poll IB state once and return a normalized payload for the UI.
    def poll_once(self) -> dict:
        messages: list[str] = []

        status = self._status_client.get_status_snapshot()
        connection_state = self._status_client.get_connection_state()
        connected = connection_state == "connected"
        now = time.monotonic()

        ticks = self.ib_client.process_messages() if connected else []
        if not isinstance(ticks, list):
            ticks = []
        else:
            ticks = [tick for tick in ticks if isinstance(tick, dict)]

        need_snapshot = False
        if connected:
            need_snapshot = (now - self._last_snapshot_monotonic) * 1000 >= self._snapshot_interval_ms

        if connected:
            if ticks:
                if self._no_tick_warning_emitted:
                    messages.append("[INFO][market_data] tick stream resumed.")
                self._has_received_stream_ticks = True
                self._no_tick_check_started_at = None
                self._no_tick_check_count = 0
                self._no_tick_warning_emitted = False
            else:
                # Startup sanity check: only run no-tick tests until first tick is seen.
                if not self._has_received_stream_ticks:
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
        else:
            self._no_tick_check_started_at = None
            self._no_tick_check_count = 0
            self._no_tick_warning_emitted = False
            self._has_received_stream_ticks = False

        orders_payload = None
        portfolio_payload = None
        if need_snapshot:
            open_orders = self._collect_open_orders(self._orders_client)
            fills = self._collect_recent_orders(self._orders_client)
            summary, positions = self._portfolio_client.get_portfolio_snapshot()
            orders_payload = {"open_orders": open_orders, "fills": fills}
            portfolio_payload = {"summary": summary, "positions": positions}
            self._last_snapshot_monotonic = now

        return {
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
