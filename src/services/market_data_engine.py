"""
Market Data Engine — Thread 1.
Polls IB tick stream (100ms), status snapshot, and account summary (10s).
Uses shared IB connection (client_id=1).

Optional Redis bus (R3) : when ``redis_url`` is provided, the engine spawns
its own asyncio event loop in the thread and drives the async publishers
(``publish_tick``, ``publish_account``, ``set_heartbeat``) through it.
Each Redis call is wrapped in try/except so a Redis outage degrades to
warn-log without crashing the engine.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Any

from redis import asyncio as aioredis
from redis import exceptions as redis_exc

from bus import keys
from bus.publisher import publish_account, publish_tick, set_heartbeat
from services.ib_client import IBClient

logger = logging.getLogger("market_data_engine")

# Transient errors worth swallowing — engine continues running.
_REDIS_SWALLOW: tuple[type[BaseException], ...] = (
    redis_exc.ConnectionError,
    redis_exc.TimeoutError,
    ConnectionError,
    TimeoutError,
    OSError,
)


class MarketDataEngine(threading.Thread):
    NO_TICK_CHECK_SECONDS = 2.0
    NO_TICK_CHECK_REPETITIONS = 3

    def __init__(
        self,
        ib_client: IBClient,
        ui_queue_post: Callable[[Callable[[], None]], None],
        interval_ms: int = 100,
        snapshot_interval_s: float = 10.0,
        symbol: str = "EURUSD",
        redis_url: str | None = None,
    ) -> None:
        """Initialize the market data engine.

        Args:
            ib_client: Shared IB client wrapper for market data connection.
            ui_queue_post: Callback to schedule a callable on the Qt main thread.
            interval_ms: Tick polling interval in milliseconds.
            snapshot_interval_s: Seconds between account/portfolio snapshots.
            symbol: Market symbol the engine publishes for (used as the
                ``{symbol}`` component of Redis keys like latest_spot:EURUSD).
            redis_url: If provided, the engine spins up an asyncio event loop
                and publishes ticks/account/heartbeat to Redis. ``None``
                disables the Redis bus entirely (backward compat before R3).
        """
        super().__init__(name="MarketDataEngine", daemon=True)
        self._ib = ib_client
        self._post_ui = ui_queue_post
        self._interval_s = max(0.025, interval_ms / 1000.0)
        self._snapshot_interval_s = snapshot_interval_s
        self._stop_event = threading.Event()
        self._symbol = symbol
        self._redis_url = redis_url

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

        # Redis bus state — created inside run() on the engine's thread
        # so the asyncio queue/event objects bind to that loop.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._redis_client: aioredis.Redis | None = None

    def set_risk_engine(self, risk_engine: Any) -> None:
        """Set the risk engine reference for spot price sharing."""
        self._risk_engine = risk_engine

    def stop(self) -> None:
        """Signal the engine thread to stop."""
        self._stop_event.set()

    def run(self) -> None:
        """Main loop: poll ticks, snapshots, publish to Redis, post to UI."""
        self._init_redis_bus_if_configured()
        last_snapshot = 0.0
        try:
            while not self._stop_event.wait(timeout=self._interval_s):
                try:
                    now = time.monotonic()
                    payload = self._poll_once(now, last_snapshot)
                    account_pushed = (now - last_snapshot) >= self._snapshot_interval_s
                    if account_pushed:
                        last_snapshot = now
                    self._post_ui(lambda p=payload: self.on_payload(p))
                    # Redis writes — each helper catches its own errors so a
                    # flapping Redis never crashes the engine loop.
                    self._publish_ticks_to_redis(payload)
                    if account_pushed:
                        self._publish_account_to_redis(payload)
                    self._set_heartbeat_to_redis()
                except Exception as exc:
                    msg = str(exc)
                    self._post_ui(lambda m=msg: self.on_payload({"error": m}))
        finally:
            self._teardown_redis_bus()

    # ── Redis bus wiring (R3 PR #4) ────────────────────────────────────────

    def _init_redis_bus_if_configured(self) -> None:
        """Spin up an asyncio loop + aioredis client if ``redis_url`` is set."""
        if not self._redis_url:
            return
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            pool = aioredis.ConnectionPool.from_url(
                self._redis_url, max_connections=10, decode_responses=True
            )
            self._redis_client = aioredis.Redis(connection_pool=pool)
            logger.info("MarketDataEngine connected to Redis at %s", self._redis_url)
        except Exception:
            # Do not crash the engine if Redis is misconfigured ; just
            # disable the bus for this run.
            logger.exception("MarketDataEngine Redis init failed, bus disabled")
            self._redis_client = None

    def _teardown_redis_bus(self) -> None:
        if self._loop is None:
            return
        try:
            if self._redis_client is not None:
                self._loop.run_until_complete(self._redis_client.aclose())
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        except Exception:
            logger.exception("MarketDataEngine Redis teardown error")
        finally:
            self._loop.close()
            self._loop = None
            self._redis_client = None

    def _publish_ticks_to_redis(self, payload: dict[str, Any]) -> None:
        """Write the most recent tick from the payload to Redis (best-effort)."""
        if self._redis_client is None or self._loop is None:
            return
        ticks = payload.get("ticks") or []
        if not ticks:
            return
        last = ticks[-1]
        bid = self._safe_float(last.get("bid"))
        ask = self._safe_float(last.get("ask"))
        if bid is None or ask is None:
            return
        mid = (bid + ask) / 2.0
        try:
            self._loop.run_until_complete(
                publish_tick(self._redis_client, self._symbol, bid, ask, mid)
            )
        except _REDIS_SWALLOW as e:
            logger.warning("redis publish_tick failed (transient): %s", e)
        except Exception:
            logger.exception("redis publish_tick unexpected error")

    def _publish_account_to_redis(self, payload: dict[str, Any]) -> None:
        """Publish a compact account snapshot to Redis (best-effort)."""
        if self._redis_client is None or self._loop is None:
            return
        portfolio = payload.get("portfolio_payload")
        if not isinstance(portfolio, dict):
            return
        summary = portfolio.get("summary") or []
        positions = portfolio.get("positions") or []
        body: dict[str, Any] = {
            "symbol": self._symbol,
            "summary_tag_count": len(summary),
            "open_positions_count": len(positions),
        }
        try:
            self._loop.run_until_complete(
                publish_account(self._redis_client, body)
            )
        except _REDIS_SWALLOW as e:
            logger.warning("redis publish_account failed (transient): %s", e)
        except Exception:
            logger.exception("redis publish_account unexpected error")

    def _set_heartbeat_to_redis(self) -> None:
        """Refresh heartbeat:market_data with the current timestamp."""
        if self._redis_client is None or self._loop is None:
            return
        try:
            self._loop.run_until_complete(
                set_heartbeat(self._redis_client, keys.ENGINE_MARKET_DATA)
            )
        except _REDIS_SWALLOW as e:
            logger.warning("redis heartbeat failed (transient): %s", e)
        except Exception:
            logger.exception("redis heartbeat unexpected error")

    def _poll_once(self, now: float, last_snapshot: float) -> dict[str, Any]:
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
        """Compute mid-price from latest bid/ask, or return whichever is available."""
        b, a = self.latest_bid, self.latest_ask
        if b and a and b > 0 and a > 0:
            return (b + a) / 2.0
        return b or a

    def _check_no_ticks(self, now: float, has_ticks: bool) -> list[str]:
        """Emit warnings if no ticks arrive within the startup grace period."""
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
        """Reset no-tick warning counters."""
        self._no_tick_check_started_at = None
        self._no_tick_check_count = 0
        self._no_tick_warning_emitted = False

    def on_payload(self, payload: dict[str, Any]) -> None:
        """Callback set by controller to route market data to UI panels."""

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Convert value to positive float, returning None on NaN or non-positive."""
        if value is None:
            return None
        try:
            f = float(value)
            return f if not math.isnan(f) and f > 0 else None
        except (TypeError, ValueError):
            return None
