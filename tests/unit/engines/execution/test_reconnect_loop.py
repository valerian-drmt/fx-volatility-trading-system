"""Execution-engine IB reconnect watchdog (ENG-1).

The lifespan task ``shared.ib_connection.maintain_ib_connection`` wraps the
already-idempotent ``OrderExecutor.connect``. Scenario locked here : the
startup connect failed (executor never connected), the watchdog keeps
retrying, and once the fake gateway accepts no further calls are made.
"""
from __future__ import annotations

import asyncio

from shared.ib_connection import maintain_ib_connection


class FakeExecutor:
    """OrderExecutor stand-in : connect() swallows failures (like the real one)."""

    def __init__(self, gateway_up_after_calls: int) -> None:
        self._connected = False
        self.connect_calls = 0
        self._up_after = gateway_up_after_calls

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, timeout: float = 5.0) -> None:
        self.connect_calls += 1
        if self.connect_calls >= self._up_after:
            self._connected = True
        # else : swallow the failure, exactly like OrderExecutor.connect


async def test_watchdog_recovers_failed_startup_connect():
    # Startup connect failed (call 1..4 fail) — the watchdog must keep
    # retrying until the gateway comes back, then go quiet.
    executor = FakeExecutor(gateway_up_after_calls=5)
    stop = asyncio.Event()
    task = asyncio.create_task(
        maintain_ib_connection(executor, stop, interval_s=0.01)
    )
    for _ in range(300):
        if executor.is_connected():
            break
        await asyncio.sleep(0.01)
    assert executor.is_connected()
    assert executor.connect_calls == 5

    # Connected : the watchdog polls but never calls connect() again.
    await asyncio.sleep(0.05)
    assert executor.connect_calls == 5

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_watchdog_reconnects_after_midsession_drop():
    executor = FakeExecutor(gateway_up_after_calls=1)
    stop = asyncio.Event()
    task = asyncio.create_task(
        maintain_ib_connection(executor, stop, interval_s=0.01)
    )
    for _ in range(100):
        if executor.is_connected():
            break
        await asyncio.sleep(0.01)
    assert executor.connect_calls == 1

    # Nightly gateway restart : session drops, watchdog must reconnect.
    executor._connected = False
    for _ in range(100):
        if executor.is_connected():
            break
        await asyncio.sleep(0.01)
    assert executor.is_connected()
    assert executor.connect_calls == 2

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
