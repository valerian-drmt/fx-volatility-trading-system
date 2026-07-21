"""Unit tests for shared.ib_connection — reconnect/backoff, no live IB.

Covers :
  * ``connect_ib_with_backoff`` retries with the shared backoff schedule
    until the fake gateway accepts, and honours ``max_attempts``.
  * ``maintain_ib_connection`` (execution-engine watchdog) keeps calling
    ``connect()`` while disconnected, stops calling once connected, and
    exits on the stop event.
"""
from __future__ import annotations

import asyncio

import pytest

from shared.backoff import next_backoff_seconds
from shared.ib_connection import connect_ib_with_backoff, maintain_ib_connection


class FakeIB:
    """Scripted ib_insync.IB stand-in : fails N connects, then succeeds."""

    def __init__(self, fail_first_n: int = 0) -> None:
        self.connected = False
        self.connect_calls = 0
        self._fail_first_n = fail_first_n

    def isConnected(self) -> bool:
        return self.connected

    async def connectAsync(self, host, port, clientId, timeout=5.0):
        self.connect_calls += 1
        if self.connect_calls <= self._fail_first_n:
            raise ConnectionRefusedError("gateway down")
        self.connected = True


async def test_connect_retries_with_backoff(monkeypatch):
    sleeps: list[float] = []

    async def _fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    ib = FakeIB(fail_first_n=2)
    await connect_ib_with_backoff(ib, host="h", port=1, client_id=9)
    assert ib.connected is True
    assert ib.connect_calls == 3
    # One backoff sleep per failed attempt, following the shared schedule.
    assert sleeps == [next_backoff_seconds(0), next_backoff_seconds(1)]


async def test_connect_noop_when_already_connected():
    ib = FakeIB()
    ib.connected = True
    await connect_ib_with_backoff(ib, host="h", port=1, client_id=9)
    assert ib.connect_calls == 0


async def test_connect_max_attempts_raises(monkeypatch):
    async def _fake_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    ib = FakeIB(fail_first_n=99)
    with pytest.raises(ConnectionError):
        await connect_ib_with_backoff(ib, host="h", port=1, client_id=9, max_attempts=3)
    assert ib.connect_calls == 3


class FakeExecutor:
    """OrderExecutor stand-in : idempotent, failure-swallowing connect()."""

    def __init__(self, connect_after_n_calls: int) -> None:
        self._connected = False
        self.connect_calls = 0
        self._succeed_at = connect_after_n_calls

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, timeout: float = 5.0) -> None:
        self.connect_calls += 1
        if self.connect_calls >= self._succeed_at:
            self._connected = True


async def test_maintain_ib_connection_reconnects_then_idles():
    executor = FakeExecutor(connect_after_n_calls=3)
    stop = asyncio.Event()
    task = asyncio.create_task(
        maintain_ib_connection(executor, stop, interval_s=0.01)
    )
    # Wait until the watchdog reconnected (3 connect attempts needed).
    for _ in range(200):
        if executor.is_connected():
            break
        await asyncio.sleep(0.01)
    assert executor.is_connected()
    assert executor.connect_calls == 3
    # Once connected : no further connect() calls.
    await asyncio.sleep(0.05)
    assert executor.connect_calls == 3
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_maintain_ib_connection_survives_connect_exception():
    class ExplodingExecutor:
        def __init__(self) -> None:
            self.connect_calls = 0

        def is_connected(self) -> bool:
            return False

        async def connect(self, timeout: float = 5.0) -> None:
            self.connect_calls += 1
            raise RuntimeError("boom")

    executor = ExplodingExecutor()
    stop = asyncio.Event()
    task = asyncio.create_task(
        maintain_ib_connection(executor, stop, interval_s=0.01)
    )
    await asyncio.sleep(0.05)
    # The loop must survive exceptions and keep retrying.
    assert executor.connect_calls >= 2
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_maintain_ib_connection_exits_on_stop():
    executor = FakeExecutor(connect_after_n_calls=1)
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(
        maintain_ib_connection(executor, stop, interval_s=0.01), timeout=1.0
    )
    assert executor.connect_calls == 0
