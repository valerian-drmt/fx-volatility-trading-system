"""VolEngine IB-reconnect behaviour (ENG-1) — mocks only, no live IB.

The engine loop must detect a dropped IB session between cycles, reconnect
via ``connect_ib_with_backoff`` and await the ``on_ib_reconnected`` hook
exactly once per reconnect ; a failing hook is logged, never fatal.
"""
from __future__ import annotations

from typing import Any

import pytest

from engines.vol.engine import VolEngine


class FakeIB:
    def __init__(self) -> None:
        self.connected = False
        self.connect_calls = 0

    def isConnected(self) -> bool:
        return self.connected

    async def connectAsync(self, host, port, clientId, timeout=5.0):
        self.connect_calls += 1
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False


class FakeRedis:
    async def get(self, name: str) -> Any:
        return None

    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        return True

    async def publish(self, channel: str, message: str) -> int:
        return 0


def _make_engine(ib: FakeIB, hook=None) -> VolEngine:
    async def _no_chain(_f: float) -> dict:
        return {}

    return VolEngine(
        ib=ib, redis=FakeRedis(), symbol="EURUSD",
        ib_host="h", ib_port=1, client_id=2,
        fetch_fop_chain=_no_chain,
        on_ib_reconnected=hook,
    )


@pytest.fixture
def fast_cycle(monkeypatch):
    """Collapse the 180 s cadence so run() spins instantly in tests."""
    monkeypatch.setattr("engines.vol.engine.CYCLE_S", 0.0)
    monkeypatch.setattr("engines.vol.engine.SKIP_BACKOFF_S", 0.0)


async def test_reconnects_and_runs_hook_once(fast_cycle):
    ib = FakeIB()
    hook_calls: list[int] = []

    async def _hook() -> None:
        hook_calls.append(1)

    engine = _make_engine(ib, hook=_hook)
    engine._cycles_since_prune = 0  # skip the DB prune in unit tests
    cycles = 0

    async def _fake_cycle() -> bool:
        nonlocal cycles
        cycles += 1
        if cycles == 1:
            ib.connected = False  # simulate the nightly gateway restart
        if cycles >= 2:
            engine.request_stop()
        return True

    engine.run_cycle = _fake_cycle  # type: ignore[method-assign]
    await engine.run()

    # Initial connect + one reconnect ; hook only for the reconnect.
    assert ib.connect_calls == 2
    assert hook_calls == [1]
    assert cycles == 2


async def test_hook_exception_is_not_fatal(fast_cycle):
    ib = FakeIB()

    async def _bad_hook() -> None:
        raise RuntimeError("re-arm failed")

    engine = _make_engine(ib, hook=_bad_hook)
    engine._cycles_since_prune = 0
    cycles = 0

    async def _fake_cycle() -> bool:
        nonlocal cycles
        cycles += 1
        if cycles == 1:
            ib.connected = False
        if cycles >= 2:
            engine.request_stop()
        return True

    engine.run_cycle = _fake_cycle  # type: ignore[method-assign]
    await engine.run()  # must not raise

    assert ib.connect_calls == 2
    assert cycles == 2


async def test_no_reconnect_while_session_up(fast_cycle):
    ib = FakeIB()
    hook_calls: list[int] = []

    async def _hook() -> None:
        hook_calls.append(1)

    engine = _make_engine(ib, hook=_hook)
    engine._cycles_since_prune = 0
    cycles = 0

    async def _fake_cycle() -> bool:
        nonlocal cycles
        cycles += 1
        if cycles >= 3:
            engine.request_stop()
        return True

    engine.run_cycle = _fake_cycle  # type: ignore[method-assign]
    await engine.run()

    assert ib.connect_calls == 1   # startup connect only
    assert hook_calls == []        # hook is reconnect-only
