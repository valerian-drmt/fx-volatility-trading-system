"""Unit tests for ``engines.market_data.engine.MarketDataEngine``.

All I/O is mocked — no real IB Gateway, no real Redis. Stop-conditions
are driven by the injected ``fetch_latest_tick`` callable so the tests
never hit a real timer.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from engines.market_data.engine import HEARTBEAT_EVERY_N_POLLS, MarketDataEngine


def _fake_ib(connected: bool = True) -> MagicMock:
    ib = MagicMock()
    ib.isConnected = MagicMock(return_value=connected)
    ib.connectAsync = AsyncMock()
    ib.disconnect = MagicMock()
    return ib


def _fake_redis() -> MagicMock:
    r = MagicMock()
    r.publish = AsyncMock(return_value=1)
    r.set = AsyncMock()
    return r


def _patch_engine_waits(monkeypatch):
    """Make ``asyncio.wait_for`` a no-wait TimeoutError inside the engine
    so the loop body runs at wire speed."""

    async def fast_wait_for(coro, timeout):
        try:
            coro.close()
        except (AttributeError, RuntimeError):
            pass
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)


@pytest.mark.asyncio
async def test_engine_publishes_tick_and_emits_heartbeat(monkeypatch):
    """Steady tick stream → publish_tick called, heartbeat fires every N polls."""
    from bus import publisher

    published: list[tuple[str, float, float, float]] = []
    heartbeats: list[str] = []

    async def fake_publish_tick(_redis, symbol, bid, ask, mid):
        published.append((symbol, bid, ask, mid))
        return 1

    async def fake_set_heartbeat(_redis, name):
        heartbeats.append(name)

    monkeypatch.setattr(publisher, "publish_tick", fake_publish_tick)
    monkeypatch.setattr(publisher, "set_heartbeat", fake_set_heartbeat)
    _patch_engine_waits(monkeypatch)

    ib = _fake_ib(connected=True)
    state = {"count": 0}
    STOP_AFTER = HEARTBEAT_EVERY_N_POLLS + 2

    def fetch_tick() -> dict | None:
        state["count"] += 1
        if state["count"] >= STOP_AFTER:
            engine.request_stop()
        return {"bid": 1.0849, "ask": 1.0851, "mid": 1.0850}

    engine = MarketDataEngine(
        ib=ib,
        redis=_fake_redis(),
        symbol="EURUSD",
        ib_host="ib-gateway",
        ib_port=4002,
        client_id=1,
        fetch_latest_tick=fetch_tick,
    )

    await engine.run()

    assert len(published) >= HEARTBEAT_EVERY_N_POLLS
    assert published[0] == ("EURUSD", 1.0849, 1.0851, 1.0850)
    assert "market_data" in heartbeats


@pytest.mark.asyncio
async def test_engine_skips_publish_when_no_tick_available(monkeypatch):
    from bus import publisher

    calls = {"publish": 0, "hb": 0}

    async def fake_publish_tick(*_a, **_kw):
        calls["publish"] += 1
        return 1

    async def fake_set_heartbeat(*_a, **_kw):
        calls["hb"] += 1

    monkeypatch.setattr(publisher, "publish_tick", fake_publish_tick)
    monkeypatch.setattr(publisher, "set_heartbeat", fake_set_heartbeat)
    _patch_engine_waits(monkeypatch)

    ib = _fake_ib(connected=True)
    state = {"count": 0}

    def fetch_tick():
        state["count"] += 1
        if state["count"] >= 3:
            engine.request_stop()
        return None

    engine = MarketDataEngine(
        ib=ib,
        redis=_fake_redis(),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=1,
        fetch_latest_tick=fetch_tick,
    )

    await engine.run()
    assert calls["publish"] == 0


@pytest.mark.asyncio
async def test_engine_reconnects_through_backoff_when_ib_down(monkeypatch):
    """ConnectionRefused retried via shared.ib_connection backoff."""
    from bus import publisher

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "publish_tick", noop)
    monkeypatch.setattr(publisher, "set_heartbeat", noop)
    _patch_engine_waits(monkeypatch)

    ib = _fake_ib(connected=False)
    attempts = {"count": 0}

    async def flaky_connect(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionRefusedError("gateway restarting")
        ib.isConnected = MagicMock(return_value=True)

    ib.connectAsync = AsyncMock(side_effect=flaky_connect)

    async def fast_sleep(_: float) -> None:
        return None

    # Patch sleep in shared.ib_connection (the backoff path) — NOT globally,
    # else the stop flag would never be reached (but we stop via counter
    # below so it doesn't matter here).
    import shared.ib_connection as ibc

    monkeypatch.setattr(ibc.asyncio, "sleep", fast_sleep)

    state = {"count": 0}

    def fetch_tick():
        state["count"] += 1
        if state["count"] >= 2:
            engine.request_stop()
        return None

    engine = MarketDataEngine(
        ib=ib,
        redis=_fake_redis(),
        symbol="EURUSD",
        ib_host="ib-gateway",
        ib_port=4002,
        client_id=1,
        fetch_latest_tick=fetch_tick,
    )

    await engine.run()
    assert attempts["count"] >= 3


@pytest.mark.asyncio
async def test_engine_disconnects_ib_on_graceful_stop(monkeypatch):
    """request_stop() must reach ib.disconnect() via the finally block."""
    from bus import publisher

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "publish_tick", noop)
    monkeypatch.setattr(publisher, "set_heartbeat", noop)
    _patch_engine_waits(monkeypatch)

    ib = _fake_ib(connected=True)
    state = {"count": 0}

    def fetch_tick():
        state["count"] += 1
        if state["count"] >= 2:
            engine.request_stop()
        return {"bid": 1.08, "ask": 1.081, "mid": 1.0805}

    engine = MarketDataEngine(
        ib=ib,
        redis=_fake_redis(),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=1,
        fetch_latest_tick=fetch_tick,
    )

    await engine.run()
    ib.disconnect.assert_called_once()
