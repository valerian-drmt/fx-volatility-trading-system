"""Redis to WebSocket bridge (#339, audit 07#7): subscribes to every channel
the WS router serves, rebroadcasts ``message`` and ``pmessage`` frames on
their concrete channel, ignores subscribe acks, survives a Redis drop by
resubscribing, and closes the pubsub on every exit path."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from redis import exceptions as redis_exc

from api.ws import redis_bridge
from bus import channels

pytestmark = pytest.mark.asyncio


class _PubSub:
    def __init__(self, frames: list, *, subscribe_raises: BaseException | None = None) -> None:
        self.frames = frames
        self.subscribe_raises = subscribe_raises
        self.subscribed: list[str] = []
        self.psubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *names: str) -> None:
        if self.subscribe_raises is not None:
            raise self.subscribe_raises
        self.subscribed.extend(names)

    async def psubscribe(self, *patterns: str) -> None:
        self.psubscribed.extend(patterns)

    async def listen(self):
        for f in self.frames:
            if isinstance(f, BaseException):
                raise f
            yield f

    async def aclose(self) -> None:
        self.closed = True


class _Redis:
    def __init__(self, pubsubs: list[_PubSub]) -> None:
        self._pubsubs = list(pubsubs)
        self.created: list[_PubSub] = []

    def pubsub(self) -> _PubSub:
        ps = self._pubsubs.pop(0)
        self.created.append(ps)
        return ps


class _Manager:
    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, str]] = []

    async def broadcast(self, channel: str, message: str) -> None:
        self.broadcasts.append((channel, message))


async def test_bridge_forwards_messages_and_pmessages_then_resubscribes_after_a_drop(monkeypatch) -> None:
    monkeypatch.setattr(redis_bridge, "_RECONNECT_BACKOFF_S", 0.0)
    first = _PubSub([
        {"type": "subscribe", "channel": channels.CH_RISK_UPDATE, "data": 1},
        {"type": "message", "channel": channels.CH_RISK_UPDATE, "data": '{"greeks": {}}'},
        {"type": "pmessage", "pattern": channels.CH_ORDERS_PATTERN,
         "channel": channels.orders_channel(7), "data": '{"state": "filled"}'},
        {"type": "psubscribe", "channel": channels.CH_ORDERS_PATTERN, "data": 1},
        redis_exc.ConnectionError("redis went away"),
    ])
    second = _PubSub([], subscribe_raises=asyncio.CancelledError())  # lifespan shutdown
    redis = _Redis([first, second])
    manager = _Manager()

    with pytest.raises(asyncio.CancelledError):
        await redis_bridge.redis_to_ws_bridge(redis, manager)  # type: ignore[arg-type]

    assert set(first.subscribed) == set(redis_bridge._FORWARDED)
    assert list(first.psubscribed) == list(redis_bridge._FORWARDED_PATTERNS)
    assert manager.broadcasts == [
        (channels.CH_RISK_UPDATE, '{"greeks": {}}'),
        (channels.orders_channel(7), '{"state": "filled"}'),
    ], "acks are ignored; pattern frames go out on the concrete channel"
    assert len(redis.created) == 2, "a dropped connection is followed by a fresh pubsub"
    assert first.closed and second.closed, "pubsub closed on the error path and on cancel"


async def test_an_unexpected_error_inside_listen_does_not_kill_the_bridge(monkeypatch) -> None:
    monkeypatch.setattr(redis_bridge, "_RECONNECT_BACKOFF_S", 0.0)
    first = _PubSub([
        {"type": "message", "channel": channels.CH_TICKS, "data": "1"},
        ValueError("malformed frame"),
    ])
    second = _PubSub([{"type": "message", "channel": channels.CH_TICKS, "data": "2"},
                      asyncio.CancelledError()])
    manager = _Manager()
    with pytest.raises(asyncio.CancelledError):
        await redis_bridge.redis_to_ws_bridge(_Redis([first, second]), manager)  # type: ignore[arg-type]
    assert manager.broadcasts == [(channels.CH_TICKS, "1"), (channels.CH_TICKS, "2")]


def test_every_channel_the_ws_router_serves_is_forwarded() -> None:
    """A new ``/ws/...`` endpoint on a channel the bridge does not subscribe
    to would connect fine and never receive a frame."""
    src = (Path(__file__).resolve().parents[4] / "src" / "api" / "routers" / "ws.py").read_text(encoding="utf-8")
    served = {getattr(channels, name) for name in re.findall(r"channels\.(CH_[A-Z_]+)", src)}
    assert served, "the router reads its channel names from bus.channels"
    assert served <= set(redis_bridge._FORWARDED), served - set(redis_bridge._FORWARDED)
    if "orders_channel(" in src:
        assert channels.CH_ORDERS_PATTERN in redis_bridge._FORWARDED_PATTERNS
