"""Async MarketDataEngine — standalone service version.

Equivalent to the threaded ``src/services/market_data_engine.py`` of the
monolith, but :

- 100% asyncio (no ``threading.Thread``, no ``queue.Queue``)
- Publishes ticks directly to Redis (throttled) via ``bus.publisher``
- Emits a heartbeat every cycle so the Docker healthcheck can poll
- Reconnects via ``shared.ib_connection.connect_ib_with_backoff``

The engine does not own Redis / IB singletons — they are injected from
``services.market_data.main`` so the unit tests pass in stubs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from bus import keys, publisher

logger = logging.getLogger(__name__)

# Default tick-poll cadence. Ticks arrive asynchronously from IB, the poll
# just drains whatever accumulated since the last iteration.
POLL_INTERVAL_S = 0.1
HEARTBEAT_EVERY_N_POLLS = 10  # ~1s at default cadence


class _RedisLike(Protocol):
    async def publish(self, channel: str, message: str) -> int: ...
    async def set(self, name: str, value: str, ex: int | None = ...) -> Any: ...


class _IBLike(Protocol):
    def isConnected(self) -> bool: ...
    async def connectAsync(self, host: str, port: int, clientId: int, timeout: float = ...) -> Any: ...
    def disconnect(self) -> None: ...


class MarketDataEngine:
    """Long-running async task : stream IB ticks, push to Redis."""

    def __init__(
        self,
        *,
        ib: _IBLike,
        redis: _RedisLike,
        symbol: str,
        ib_host: str,
        ib_port: int,
        client_id: int,
        fetch_latest_tick: Any,
    ) -> None:
        self.ib = ib
        self.redis = redis
        self.symbol = symbol
        self.ib_host = ib_host
        self.ib_port = ib_port
        self.client_id = client_id
        # Injected callable so tests can substitute IB's ticker stream.
        # Signature : () -> dict | None with keys {bid, ask, mid, ts}.
        self._fetch_latest_tick = fetch_latest_tick
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        """Signal the main loop to exit at the next iteration."""
        self._stop.set()

    async def run(self) -> None:
        """Main engine loop : connect, poll, publish, heartbeat, repeat."""
        from shared.ib_connection import connect_ib_with_backoff
        from shared.observability import observed_cycle

        await connect_ib_with_backoff(
            self.ib, host=self.ib_host, port=self.ib_port, client_id=self.client_id
        )
        logger.info("market_data_engine_started", extra={"symbol": self.symbol})

        try:
            poll = 0
            while not self._stop.is_set():
                # P0 obs : each 100 ms poll = one cycle. Prometheus counters
                # handle the rate fine. cycle_id rotates per poll keeping logs
                # cleanly segmented.
                with observed_cycle("market_data"):
                    await self._poll_once()
                poll += 1
                if poll % HEARTBEAT_EVERY_N_POLLS == 0:
                    await publisher.set_heartbeat(self.redis, keys.ENGINE_MARKET_DATA)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=POLL_INTERVAL_S)
                    break  # stop requested during sleep
                except TimeoutError:
                    continue  # normal — no stop requested, loop
        finally:
            self._teardown()

    async def _poll_once(self) -> None:
        tick = self._fetch_latest_tick()
        if not tick:
            return
        try:
            await publisher.publish_tick(
                self.redis,
                self.symbol,
                bid=tick.get("bid"),
                ask=tick.get("ask"),
                mid=tick.get("mid"),
            )
        except Exception:
            logger.exception("publish_tick_failed")

    def _teardown(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            logger.exception("ib_disconnect_failed")
        logger.info("market_data_engine_stopped", extra={"symbol": self.symbol})
