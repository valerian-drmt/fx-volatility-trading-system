"""Unit tests for engines.db_writer.service.DbWriterService.

Lock the silent-death fix (DBW-1) :
  * ``redis.exceptions.ConnectionError`` / ``RedisError`` out of
    ``pubsub.listen()`` triggers a re-subscribe instead of killing the
    ``db_events_subscriber`` task (the redis exceptions subclass only
    ``RedisError``, NOT the builtins of the same name).
  * The heartbeat is suppressed while the subscriber is down, and
    resumes once the subscription is live again.
  * ``run()`` supervises the subscriber task and restarts it if it ever
    dies with an unexpected exception.
  * Existing behaviours stay locked : malformed frame → warning,
    ``QueueFull`` → drop + warning.

No real Redis anywhere — the pubsub is a scripted fake.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

import engines.db_writer.service as service_mod
from engines.db_writer.service import DbWriterService

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


# --- Fakes ------------------------------------------------------------------

class _FakeWriter:
    """Duck-typed AsyncDatabaseWriter : queue + run/shutdown no-ops."""

    def __init__(self, maxsize: int = 16) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._done = asyncio.Event()

    async def run(self) -> None:
        await self._done.wait()

    async def shutdown(self) -> None:
        self._done.set()


class _FakePubSub:
    """One subscription attempt, scripted.

    ``script`` is either an exception instance (raised from ``listen``),
    or a list of frames to yield — after which ``listen`` blocks forever
    (a live subscription never returns on its own).
    """

    def __init__(self, script: BaseException | list[dict]) -> None:
        self.script = script
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def listen(self):
        if isinstance(self.script, BaseException):
            raise self.script
        for frame in self.script:
            yield frame
        await asyncio.Event().wait()  # block forever, like a live pubsub

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    """Hands out the scripted pubsubs in order ; blocks-forever afterwards."""

    def __init__(self, pubsubs: list[_FakePubSub]) -> None:
        self._pubsubs = list(pubsubs)
        self.pubsub_calls = 0

    def pubsub(self) -> _FakePubSub:
        self.pubsub_calls += 1
        if self._pubsubs:
            return self._pubsubs.pop(0)
        return _FakePubSub([])

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        return None


def _frame(table: str = "vol_surface_history", **payload: Any) -> dict:
    return {
        "type": "message",
        "data": json.dumps({"table": table, "payload": payload or {"x": 1}}),
    }


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero out the reconnect backoff so tests don't sleep for real."""
    monkeypatch.setattr(service_mod, "next_backoff_seconds", lambda attempt: 0.0)


# --- Subscriber reconnect (the DBW-1 bug) -----------------------------------

@pytest.mark.parametrize(
    "exc",
    [RedisConnectionError("connection lost"), RedisError("generic redis error")],
    ids=["redis_connection_error", "redis_error"],
)
async def test_redis_error_triggers_resubscribe_not_death(exc: BaseException) -> None:
    """A redis-py exception out of listen() must re-subscribe, not kill the task."""
    good = _FakePubSub([_frame(x=1), _frame(x=2)])
    redis = _FakeRedis([_FakePubSub(exc), good])
    writer = _FakeWriter()
    service = DbWriterService(redis=redis, writer=writer)  # type: ignore[arg-type]

    task = asyncio.create_task(service._subscribe_loop())
    try:
        await _wait_for(lambda: writer.queue.qsize() == 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert redis.pubsub_calls == 2, "expected a re-subscribe after the redis error"
    assert good.subscribed == [service.channel]
    table, payload = writer.queue.get_nowait()
    assert table == "vol_surface_history"
    assert payload == {"x": 1}


async def test_unexpected_exception_also_retries() -> None:
    """Last-resort catch : even a non-redis exception must not kill the loop."""
    good = _FakePubSub([_frame(x=1)])
    redis = _FakeRedis([_FakePubSub(ValueError("boom")), good])
    writer = _FakeWriter()
    service = DbWriterService(redis=redis, writer=writer)  # type: ignore[arg-type]

    task = asyncio.create_task(service._subscribe_loop())
    try:
        await _wait_for(lambda: writer.queue.qsize() == 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert redis.pubsub_calls == 2


# --- Heartbeat tied to subscriber liveness ----------------------------------

async def test_heartbeat_suppressed_while_subscriber_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats: list[str] = []

    async def _fake_heartbeat(redis: Any, engine_name: str, timestamp: Any = None) -> None:
        beats.append(engine_name)

    monkeypatch.setattr(service_mod.publisher, "set_heartbeat", _fake_heartbeat)
    monkeypatch.setattr(service_mod, "HEARTBEAT_INTERVAL_S", 0.005)

    writer = _FakeWriter()
    service = DbWriterService(redis=_FakeRedis([]), writer=writer)  # type: ignore[arg-type]

    task = asyncio.create_task(service._heartbeat_loop())
    try:
        # Subscriber down (initial state) → no heartbeat published.
        await asyncio.sleep(0.05)
        assert beats == []

        # Subscriber back up → heartbeats resume.
        service._subscriber_ok = True
        await _wait_for(lambda: len(beats) >= 2)

        # Down again → publishing stops.
        service._subscriber_ok = False
        await asyncio.sleep(0.02)
        count_when_down = len(beats)
        await asyncio.sleep(0.05)
        assert len(beats) == count_when_down
    finally:
        service.request_stop()
        await task


# --- run() supervision -------------------------------------------------------

async def test_run_restarts_subscriber_task_that_dies() -> None:
    """Defense in depth : a subscriber task death must be logged + restarted."""
    writer = _FakeWriter()
    service = DbWriterService(redis=_FakeRedis([]), writer=writer)  # type: ignore[arg-type]

    calls = 0
    resurrected = asyncio.Event()

    async def _dying_then_blocking() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("subscriber blew up")
        resurrected.set()
        await service._stop.wait()

    service._subscribe_loop = _dying_then_blocking  # type: ignore[method-assign]

    run_task = asyncio.create_task(service.run())
    try:
        async with asyncio.timeout(2.0):
            await resurrected.wait()
        assert calls == 2
    finally:
        service.request_stop()
        async with asyncio.timeout(2.0):
            await run_task


async def test_run_stops_cleanly_on_request_stop() -> None:
    writer = _FakeWriter()
    service = DbWriterService(redis=_FakeRedis([_FakePubSub([])]), writer=writer)  # type: ignore[arg-type]

    run_task = asyncio.create_task(service.run())
    await asyncio.sleep(0.01)
    service.request_stop()
    async with asyncio.timeout(2.0):
        await run_task
    assert writer._done.is_set(), "writer.shutdown() must run during drain"


# --- Frame handling (locked behaviours) --------------------------------------

def test_enqueue_malformed_frame_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    writer = _FakeWriter()
    service = DbWriterService(redis=_FakeRedis([]), writer=writer)  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING):
        service._enqueue("{not json at all")
    assert writer.queue.qsize() == 0
    assert any(r.message == "db_events_malformed_frame" for r in caplog.records)


def test_enqueue_queue_full_drops_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    writer = _FakeWriter(maxsize=1)
    service = DbWriterService(redis=_FakeRedis([]), writer=writer)  # type: ignore[arg-type]

    service._enqueue(json.dumps({"table": "t", "payload": {"a": 1}}))
    with caplog.at_level(logging.WARNING):
        service._enqueue(json.dumps({"table": "t", "payload": {"a": 2}}))

    assert writer.queue.qsize() == 1
    assert any(r.message == "writer_queue_full_event_dropped" for r in caplog.records)
