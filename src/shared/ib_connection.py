"""IB Gateway connection with exponential backoff.

Every engine container opens its own IB connection (distinct
``client_id`` per service : 1=market-data, 2=vol-engine, 3=risk,
5=execution-engine).
If the Gateway is down at startup — common during the 23:59 daily
restart window — the service retries with an exponential backoff capped
at 60 seconds, yielding control via ``asyncio.sleep`` so the event loop
can still process signals.

Decoupled from ``ib_insync`` so the backoff math can be unit-tested
without an IB mock.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

# Backoff math lives in shared.backoff (extracted so non-IB reconnect
# loops can reuse it) — re-exported here for backwards compatibility.
from shared.backoff import (  # noqa: F401
    MAX_BACKOFF_S,
    MIN_BACKOFF_S,
    next_backoff_seconds,
)

logger = logging.getLogger(__name__)


class _IBLike(Protocol):
    """Minimal protocol for an ``ib_insync.IB`` instance we can mock."""

    def isConnected(self) -> bool: ...
    async def connectAsync(
        self, host: str, port: int, clientId: int, timeout: float = 5.0
    ) -> Any: ...


class _ExecutorLike(Protocol):
    """Minimal protocol for an executor owning its own IB connection.

    Matches ``engines.execution.order_executor.OrderExecutor`` : ``connect``
    must be idempotent (no-op when already connected) and swallow failures.
    """

    def is_connected(self) -> bool: ...
    async def connect(self, timeout: float = 5.0) -> None: ...


async def maintain_ib_connection(
    executor: _ExecutorLike,
    stop: asyncio.Event,
    *,
    interval_s: float = 10.0,
    connect_timeout: float = 5.0,
) -> None:
    """Background watchdog for request-driven services (execution-engine).

    Cycle engines (market-data / vol / risk) re-check ``ib.isConnected()``
    at the top of their own loop and call :func:`connect_ib_with_backoff`
    inline — a service without a cycle needs this task instead. Every
    ``interval_s`` seconds it polls ``executor.is_connected()`` and, while
    disconnected, calls ``executor.connect()``. Because ``connect`` is
    idempotent and failure-swallowing, this loop both recovers from the
    nightly IB Gateway restart *and* retries a failed startup connect.

    Polling ``is_connected()`` is deliberate : ``ib.disconnectedEvent``
    delivery on a torn socket is best-effort, the poll is the guarantee.
    Exits when ``stop`` is set.
    """
    while not stop.is_set():
        if not executor.is_connected():
            try:
                await executor.connect(timeout=connect_timeout)
                if executor.is_connected():
                    logger.info("ib_watchdog_reconnected")
            except Exception:
                # connect() swallows its own failures — this is a belt for
                # unexpected errors ; the watchdog must never die.
                logger.exception("ib_watchdog_connect_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue


async def connect_ib_with_backoff(
    ib: _IBLike,
    host: str,
    port: int,
    client_id: int,
    *,
    max_attempts: int | None = None,
    timeout: float = 5.0,
) -> None:
    """Connect ``ib`` to IB Gateway, retrying with exponential backoff.

    Exits the loop when connected. ``max_attempts`` caps the retries
    (``None`` = retry forever, the normal prod behaviour since IB Gateway
    eventually comes back from the nightly restart).
    """
    # P0/P1 obs : mirror IB session state into the Prometheus gauge so the
    # Grafana "IB session uptime" panel shows UP/DOWN per clientId. Set
    # to 0 at the start of every reconnect cycle ; flip to 1 once
    # isConnected() returns True. Best-effort — never crashes if metric
    # isn't available (e.g. unit test that doesn't import observability).
    try:
        from shared.observability import ib_session_connected
        ib_session_connected.labels(client_id=str(client_id)).set(0)
    except Exception:
        ib_session_connected = None  # type: ignore[assignment]

    attempt = 0
    while True:
        if ib.isConnected():
            if ib_session_connected is not None:
                ib_session_connected.labels(client_id=str(client_id)).set(1)
            return
        try:
            await ib.connectAsync(host, port, clientId=client_id, timeout=timeout)
            if ib.isConnected():
                if ib_session_connected is not None:
                    ib_session_connected.labels(client_id=str(client_id)).set(1)
                logger.info(
                    "ib_connected",
                    extra={"host": host, "port": port, "client_id": client_id, "attempt": attempt},
                )
                return
        except (TimeoutError, ConnectionRefusedError, OSError) as exc:
            logger.warning(
                "ib_connect_failed",
                extra={"host": host, "port": port, "attempt": attempt, "error": str(exc)},
            )

        if max_attempts is not None and attempt + 1 >= max_attempts:
            raise ConnectionError(
                f"IB Gateway at {host}:{port} unreachable after {max_attempts} attempts"
            )

        await asyncio.sleep(next_backoff_seconds(attempt))
        attempt += 1
