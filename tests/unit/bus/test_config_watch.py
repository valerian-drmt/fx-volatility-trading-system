"""Convergent config watcher (#329, audit 05#12 / 05#4): applies by version
and ignores older payloads, loads the DB's latest at start and on every
resubscribe, re-reads on a timer, survives a failed subscribe and a dropped
connection, parses junk without dying."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from bus import config_watch
from bus.channels import CH_CONFIG_CHANGED
from bus.config_watch import ConfigWatcher, parse_config_message
from core.config import VolTradingConfig

pytestmark = pytest.mark.asyncio


def _cfg(strikes: int) -> VolTradingConfig:
    return VolTradingConfig.model_validate({"capacity": {"preset": "custom", "strikes_per_side": strikes}})


def _msg(version: int, strikes: int) -> dict[str, Any]:
    return {"type": "message", "channel": CH_CONFIG_CHANGED,
            "data": json.dumps({"version": version, "config": _cfg(strikes).model_dump()})}


class _PubSub:
    def __init__(self, frames: list[Any], *, fail_subscribe: bool = False) -> None:
        self.frames = list(frames)
        self.fail_subscribe = fail_subscribe
        self.closed = False

    async def subscribe(self, *_: str) -> None:
        if self.fail_subscribe:
            raise ConnectionError("redis away")

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0) -> Any:
        if not self.frames:
            await asyncio.sleep(0.01)
            return None
        f = self.frames.pop(0)
        if isinstance(f, BaseException):
            raise f
        return f

    async def unsubscribe(self, *_: str) -> None: ...
    async def close(self) -> None:
        self.closed = True


class _Redis:
    def __init__(self, pubsubs: list[_PubSub]) -> None:
        self._pubsubs = list(pubsubs)
        self.created: list[_PubSub] = []

    def pubsub(self) -> _PubSub:
        ps = self._pubsubs.pop(0) if self._pubsubs else _PubSub([])
        self.created.append(ps)
        return ps


class _Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def apply(self, version: int, cfg: VolTradingConfig) -> None:
        self.calls.append((version, cfg.capacity.strikes_per_side))


async def _run_until(watcher: ConfigWatcher, stop: asyncio.Event, predicate, timeout: float = 3.0) -> None:
    task = asyncio.create_task(watcher.run(stop))
    try:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await task


def test_parse_message_shapes() -> None:
    v, cfg = parse_config_message(json.dumps({"version": 3, "config": _cfg(9).model_dump()}).encode())
    assert v == 3 and cfg.capacity.strikes_per_side == 9
    assert parse_config_message("garbage") is None
    assert parse_config_message(json.dumps({"config": {}})) is None, "no version = not applied"


async def test_offer_applies_by_version_and_ignores_older_or_equal() -> None:
    sink = _Sink()
    w = ConfigWatcher(_Redis([]), apply=sink.apply, name="t")
    assert w.offer(2, _cfg(10)) and w.offer(5, _cfg(12))
    assert not w.offer(3, _cfg(39)) and not w.offer(5, _cfg(39))
    assert sink.calls == [(2, 10), (5, 12)] and w.version == 5 and w.skipped_old == 2


async def test_load_once_reads_the_loader_and_a_failing_loader_is_not_fatal() -> None:
    sink = _Sink()
    rows = [(4, _cfg(7))]

    async def loader():
        return rows[0]

    async def boom():
        raise RuntimeError("db down")

    w = ConfigWatcher(_Redis([]), apply=sink.apply, loader=loader, name="t")
    assert await w.load_once() is True and sink.calls == [(4, 7)]
    assert await w.load_once() is False, "same version again is not re-applied"
    w2 = ConfigWatcher(_Redis([]), apply=sink.apply, loader=boom, name="t")
    assert await w2.load_once() is False


async def test_run_loads_at_subscribe_applies_newer_messages_and_ignores_a_late_old_one() -> None:
    sink = _Sink()

    async def loader():
        return 3, _cfg(11)

    redis = _Redis([_PubSub([_msg(5, 13), _msg(4, 39), {"type": "subscribe", "data": 1}, _msg(6, 14)])])
    w = ConfigWatcher(redis, apply=sink.apply, loader=loader, name="t", poll_timeout_s=0.01)
    stop = asyncio.Event()
    await _run_until(w, stop, lambda: len(sink.calls) >= 3)
    assert sink.calls == [(3, 11), (5, 13), (6, 14)]
    assert w.skipped_old == 1


async def test_run_retries_a_failed_subscribe_then_resubscribes_and_rereads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_watch, "_BACKOFF_MIN_S", 0.01)
    sink = _Sink()

    async def loader():
        # the first successful subscribe sees version 1, every later re-read version 2
        return (1, _cfg(10)) if not sink.calls else (2, _cfg(20))

    redis = _Redis([_PubSub([], fail_subscribe=True), _PubSub([ConnectionError("dropped")]), _PubSub([])])
    w = ConfigWatcher(redis, apply=sink.apply, loader=loader, name="t", poll_timeout_s=0.01)
    stop = asyncio.Event()
    await _run_until(w, stop, lambda: len(redis.created) >= 3 and len(sink.calls) >= 2)
    assert len(redis.created) == 3, "failed subscribe, dropped connection, healthy: three attempts"
    assert sink.calls[0] == (1, 10) and sink.calls[-1] == (2, 20)
    assert all(ps.closed for ps in redis.created[1:]), "every pubsub that subscribed was closed"


async def test_periodic_reread_catches_a_version_pubsub_never_delivered() -> None:
    sink = _Sink()
    current = {"row": (1, _cfg(10))}

    async def loader():
        return current["row"]

    w = ConfigWatcher(_Redis([_PubSub([])]), apply=sink.apply, loader=loader, name="t",
                      poll_timeout_s=0.01, reread_interval_s=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    try:
        async with asyncio.timeout(3.0):
            while not sink.calls:
                await asyncio.sleep(0.01)
            current["row"] = (2, _cfg(20))
            while len(sink.calls) < 2:
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await task
    assert sink.calls == [(1, 10), (2, 20)]
