"""MarketDataEngine IB-reconnect behaviour (ENG-1) — mocks only.

After a dropped session the poll loop must reconnect AND re-run the
post-connect hook so a fresh ticker subscription replaces the dead one.
"""
from __future__ import annotations

from typing import Any

import pytest

from engines.market_data.engine import MarketDataEngine


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
    async def publish(self, channel: str, message: str) -> int:
        return 0

    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        return True


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr("engines.market_data.engine.POLL_INTERVAL_S", 0.001)


async def test_reconnect_reruns_subscription_hook(fast_poll):
    ib = FakeIB()
    hook_calls: list[int] = []

    async def _subscribe() -> None:
        hook_calls.append(1)

    polls = 0
    engine_ref: dict[str, MarketDataEngine] = {}

    def _fetch_tick() -> dict | None:
        nonlocal polls
        polls += 1
        if polls == 1:
            ib.connected = False  # nightly gateway restart between polls
        if polls >= 3:
            engine_ref["e"].request_stop()
        return None

    engine = MarketDataEngine(
        ib=ib, redis=FakeRedis(), symbol="EURUSD",
        ib_host="h", ib_port=1, client_id=1,
        fetch_latest_tick=_fetch_tick,
        post_connect_hook=_subscribe,
    )
    engine_ref["e"] = engine
    await engine.run()

    # Startup connect + reconnect after the drop ; hook ran on both.
    assert ib.connect_calls == 2
    assert hook_calls == [1, 1]
    assert polls >= 3
