"""Tests for ConnectionManager and redis_to_ws_bridge (R4 PR #7)."""
from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketState

from api.ws.connection_manager import ConnectionManager
from api.ws.redis_bridge import redis_to_ws_bridge


class _FakeWS:
    """Minimal stand-in for starlette.WebSocket — controls its CONNECTED state."""

    def __init__(self, *, fail_on_send: bool = False):
        self.application_state = WebSocketState.CONNECTED
        self.sent: list[str] = []
        self.accepted = False
        self._fail = fail_on_send

    async def accept(self):
        self.accepted = True

    async def send_text(self, msg: str):
        if self._fail:
            raise RuntimeError("simulated send failure")
        self.sent.append(msg)


# --- ConnectionManager ------------------------------------------------------


@pytest.mark.asyncio
class TestConnectionManager:
    async def test_connect_registers_and_accepts(self):
        mgr = ConnectionManager()
        ws = _FakeWS()
        await mgr.connect("ticks", ws)
        assert ws.accepted is True
        assert mgr.count("ticks") == 1

    async def test_broadcast_to_all_subscribers(self):
        mgr = ConnectionManager()
        a, b = _FakeWS(), _FakeWS()
        await mgr.connect("ticks", a)
        await mgr.connect("ticks", b)
        await mgr.broadcast("ticks", '{"mid": 1.08}')
        assert a.sent == ['{"mid": 1.08}']
        assert b.sent == ['{"mid": 1.08}']

    async def test_broadcast_drops_ws_that_errored(self):
        mgr = ConnectionManager()
        good, bad = _FakeWS(), _FakeWS(fail_on_send=True)
        await mgr.connect("ticks", good)
        await mgr.connect("ticks", bad)
        await mgr.broadcast("ticks", "msg1")
        # Bad one removed from registry after the failure.
        assert mgr.count("ticks") == 1
        # Next broadcast only hits the good one — no exception leaks.
        await mgr.broadcast("ticks", "msg2")
        assert good.sent == ["msg1", "msg2"]

    async def test_disconnected_ws_is_skipped(self):
        mgr = ConnectionManager()
        ws = _FakeWS()
        await mgr.connect("ticks", ws)
        ws.application_state = WebSocketState.DISCONNECTED
        await mgr.broadcast("ticks", "msg")
        assert ws.sent == []
        assert mgr.count("ticks") == 0

    async def test_channels_are_isolated(self):
        mgr = ConnectionManager()
        tick_ws, vol_ws = _FakeWS(), _FakeWS()
        await mgr.connect("ticks", tick_ws)
        await mgr.connect("vol_update", vol_ws)
        await mgr.broadcast("ticks", "tick_msg")
        assert tick_ws.sent == ["tick_msg"]
        assert vol_ws.sent == []


# --- redis_to_ws_bridge -----------------------------------------------------


class _FakePubSub:
    """Yields a preset sequence of messages then raises CancelledError."""

    def __init__(self, messages: list[dict]):
        self._messages = messages
        self.subscribed_to: tuple[str, ...] = ()
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed_to = channels

    async def listen(self):
        for msg in self._messages:
            yield msg
        # Emulate pubsub blocking forever — the bridge sleeps here until
        # the lifespan cancels it.
        await asyncio.sleep(10)

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub):
        self._pubsub = pubsub

    def pubsub(self) -> _FakePubSub:
        return self._pubsub


@pytest.mark.asyncio
class TestRedisBridge:
    async def test_forwards_message_to_manager(self):
        mgr = ConnectionManager()
        ws = _FakeWS()
        await mgr.connect("ticks", ws)

        pubsub = _FakePubSub([
            {"type": "subscribe", "channel": "ticks", "data": 1},   # should be ignored
            {"type": "message", "channel": "ticks", "data": '{"mid": 1.08}'},
        ])
        redis = _FakeRedis(pubsub)

        bridge = asyncio.create_task(redis_to_ws_bridge(redis, mgr))   # type: ignore[arg-type]
        await asyncio.sleep(0.05)   # let the bridge drain the two messages
        bridge.cancel()
        try:
            await bridge
        except asyncio.CancelledError:
            pass

        assert pubsub.subscribed_to == (
            "ticks", "account", "vol_update", "risk_update", "system_alerts",
        )
        assert ws.sent == ['{"mid": 1.08}']
        assert pubsub.closed is True

    async def test_ignores_non_message_frames(self):
        """Only frames with type=='message' should trigger a broadcast."""
        mgr = ConnectionManager()
        ws = _FakeWS()
        await mgr.connect("vol_update", ws)

        pubsub = _FakePubSub([
            {"type": "subscribe", "channel": "vol_update", "data": 1},
            {"type": "unsubscribe", "channel": "vol_update", "data": 0},
            {"type": "pmessage", "channel": "vol_update", "data": "ignored"},
        ])
        redis = _FakeRedis(pubsub)

        bridge = asyncio.create_task(redis_to_ws_bridge(redis, mgr))   # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        bridge.cancel()
        try:
            await bridge
        except asyncio.CancelledError:
            pass

        # Nothing was forwarded — those frame types are filtered out.
        assert ws.sent == []
