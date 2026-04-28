"""IB Gateway connection with exponential backoff.

Every R7 service container opens its own IB connection (distinct
``client_id`` per service : 1=market-data, 2=vol-engine, 3=risk).
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

logger = logging.getLogger(__name__)

MIN_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0


def next_backoff_seconds(attempt: int) -> float:
    """Return the wait before attempt ``attempt`` (0-based).

    ``attempt=0`` → 1 s, ``attempt=1`` → 2 s, ``attempt=2`` → 4 s, ...
    capped at ``MAX_BACKOFF_S``. Negative attempts clamp to the minimum.
    """
    if attempt < 0:
        return MIN_BACKOFF_S
    delay = MIN_BACKOFF_S * (2 ** attempt)
    return min(delay, MAX_BACKOFF_S)


class _IBLike(Protocol):
    """Minimal protocol for an ``ib_insync.IB`` instance we can mock."""

    def isConnected(self) -> bool: ...
    async def connectAsync(
        self, host: str, port: int, clientId: int, timeout: float = 5.0
    ) -> Any: ...


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
    attempt = 0
    while True:
        if ib.isConnected():
            return
        try:
            await ib.connectAsync(host, port, clientId=client_id, timeout=timeout)
            if ib.isConnected():
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
