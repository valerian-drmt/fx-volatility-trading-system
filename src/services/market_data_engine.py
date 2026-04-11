"""
Market Data Engine — Thread 1.
Polls IB tick stream (100ms), status snapshot, and account summary (10s).
Uses shared IB connection (client_id=1).
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

from services.ib_client import IBClient


class MarketDataEngine(threading.Thread):
    NO_TICK_CHECK_SECONDS = 2.0
    NO_TICK_CHECK_REPETITIONS = 3

    def __init__(
        self,
        ib_client: IBClient,
        ui_queue_post: Callable[[Callable], None],
        interval_ms: int = 100,
        snapshot_interval_s: float = 10.0,
    ) -> None:
        super().__init__(name="MarketDataEngine", daemon=True)
        self._ib = ib_client
        self._post_ui = ui_queue_post
        self._interval_s = max(0.025, interval_ms / 1000.0)
        self._snapshot_interval_s = snapshot_interval_s
        self._stop_event = threading.Event()

        # Shared output: controller reads these directly
        self.latest_bid: float | None = None
        self.latest_ask: float | None = None

        # Reference to risk engine for spot sharing (set by controller)
        self._risk_engine: Any = None

        # No-tick check state
        self._no_tick_check_started_at: float | None = None
        self._no_tick_check_count = 0
        self._no_tick_warning_emitted = False
        self._has_received_stream_ticks = False

    def set_risk_engine(self, risk_engine: Any) -> None:
        self._risk_engine = risk_engine

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        last_snapshot = 0.0
        while not self._stop_event.wait(timeout=self._interval_s):
            try:
                now = time.monotonic()
                payload = self._poll_once(now, last_snapshot)
                if now - last_snapshot >= self._snapshot_interval_s:
                    last_snapshot = now
                self._post_ui(lambda p=payload: self.on_payload(p))
            except Exception as exc:
                msg = str(exc)
                self._post_ui(lambda m=msg: self.on_payload({"error": m}))

    def _poll_once(self, now: float, last_snapshot: float) -> dict:
        messages: list[str] = []
        status = self._ib.get_status_snapshot()
        connection_state = self._ib.get_connection_state()
        connected = connection_state == "connected"

        ticks = self._ib.process_messages() if connected else []
        if not isinstance(ticks, list):
            ticks = []
        else:
            ticks = [t for t in ticks if isinstance(t, dict)]

        # Update latest bid/ask + push spot to risk engine
        if ticks:
            for t in ticks:
                b = self._safe_float(t.get("bid"))
                a = self._safe_float(t.get("ask"))
                if b is not None:
                    self.latest_bid = b
                if a is not None:
                    self.latest_ask = a
            if self._risk_engine is not None:
                mid = self._mid_spot()
                if mid and mid > 0:
                    self._risk_engine.spot = mid

        # No-tick checks (startup only)
        if connected:
            messages.extend(self._check_no_ticks(now, bool(ticks)))
        else:
            self._reset_no_tick_state()
            self._has_received_stream_ticks = False

        # Account snapshot (every snapshot_interval_s)
        portfolio_payload = None
        if connected and (now - last_snapshot) >= self._snapshot_interval_s:
            try:
                summary, positions = self._ib.get_portfolio_snapshot()
                portfolio_payload = {"summary": summary, "positions": positions}
            except Exception:
                pass

        return {
            "status": {
                "connection_state": connection_state,
                "mode": status.get("mode", "--"),
                "env": status.get("env", "--"),
                "client_id": status.get("client_id", "--"),
                "account": status.get("account", "--"),
            },
            "ticks": ticks,
            "portfolio_payload": portfolio_payload,
            "messages": messages,
        }

    def _mid_spot(self) -> float | None:
        b, a = self.latest_bid, self.latest_ask
        if b and a and b > 0 and a > 0:
            return (b + a) / 2.0
        return b or a

    def _check_no_ticks(self, now: float, has_ticks: bool) -> list[str]:
        messages: list[str] = []
        if has_ticks:
            if self._no_tick_warning_emitted:
                messages.append("[INFO][market_data] tick stream resumed.")
            self._has_received_stream_ticks = True
            self._reset_no_tick_state()
        elif not self._has_received_stream_ticks:
            if self._no_tick_check_started_at is None:
                self._no_tick_check_started_at = now
            elif not self._no_tick_warning_emitted:
                elapsed = now - self._no_tick_check_started_at
                if elapsed >= self.NO_TICK_CHECK_SECONDS:
                    self._no_tick_check_count += 1
                    self._no_tick_check_started_at = now
                    n = self._no_tick_check_count
                    if n >= self.NO_TICK_CHECK_REPETITIONS:
                        messages.append(
                            f"[WARN][market_data] no ticks received "
                            f"(test {self.NO_TICK_CHECK_REPETITIONS}/{self.NO_TICK_CHECK_REPETITIONS}); "
                            "market may be closed or data is unavailable for this symbol."
                        )
                        self._no_tick_warning_emitted = True
                    else:
                        messages.append(
                            f"[INFO][market_data] no ticks received "
                            f"(test {n}/{self.NO_TICK_CHECK_REPETITIONS})."
                        )
        return messages

    def _reset_no_tick_state(self) -> None:
        self._no_tick_check_started_at = None
        self._no_tick_check_count = 0
        self._no_tick_warning_emitted = False

    def on_payload(self, payload: dict) -> None:
        """Callback set by controller to route market data to UI panels."""

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            f = float(value)
            return f if not math.isnan(f) and f > 0 else None
        except (TypeError, ValueError):
            return None
