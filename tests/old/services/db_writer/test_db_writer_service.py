"""Unit tests for ``engines.db_writer.service.DbWriterService``.

No real Redis, no real Postgres. The Redis client is a mock with a
controllable async-generator pubsub ; the AsyncDatabaseWriter is
constructed with an in-memory SQLite engine so the real batch + commit
path executes end-to-end.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from engines.db_writer.service import DbWriterService
from persistence.models import AccountHistory, Base
from persistence.writer import AsyncDatabaseWriter
from shared.db_events import DB_EVENTS_CHANNEL


async def _build_memory_writer(batch_timeout_s: float = 0.1) -> AsyncDatabaseWriter:
    """AsyncDatabaseWriter backed by an in-memory SQLite engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return AsyncDatabaseWriter(
        session_factory=session_factory, batch_timeout_s=batch_timeout_s
    )


class _FakePubSub:
    """Minimal pubsub stub : listen() yields messages from an asyncio queue."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[dict] = asyncio.Queue()
        self.subscribed: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def aclose(self) -> None:
        pass

    async def listen(self):
        while True:
            msg = await self._q.get()
            if msg is None:  # sentinel to stop cleanly from the test
                return
            yield msg

    def feed(self, data: str) -> None:
        self._q.put_nowait({"type": "message", "channel": "db_events", "data": data})

    def feed_non_message(self) -> None:
        self._q.put_nowait({"type": "subscribe", "channel": "db_events", "data": 1})


def _fake_redis(pubsub: _FakePubSub) -> MagicMock:
    r = MagicMock()
    r.pubsub = MagicMock(return_value=pubsub)
    # For set_heartbeat calls inside _heartbeat_loop.
    r.set = AsyncMock()
    return r


def _acct_payload(net_liq: float = 100_000.0) -> dict:
    # SQLite DateTime accepts only datetime objects ; Postgres would accept
    # ISO strings, but the in-memory dialect is stricter.
    return {
        "timestamp": datetime(2026, 4, 21, 9, 0, 0, tzinfo=UTC),
        "net_liq_usd": net_liq,
        "cash_usd": net_liq / 2,
    }


@pytest.mark.asyncio
async def test_subscribe_registers_db_events_channel(monkeypatch):
    from bus import publisher

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "set_heartbeat", noop)

    pubsub = _FakePubSub()
    service = DbWriterService(redis=_fake_redis(pubsub), writer=await _build_memory_writer())

    subscribe_task = asyncio.create_task(service._subscribe_loop())
    # Give the loop one tick to call subscribe().
    await asyncio.sleep(0.05)
    subscribe_task.cancel()
    try:
        await subscribe_task
    except asyncio.CancelledError:
        pass

    assert pubsub.subscribed == [DB_EVENTS_CHANNEL]


@pytest.mark.asyncio
async def test_enqueue_parses_frame_and_pushes_to_writer():
    pubsub = _FakePubSub()
    writer = await _build_memory_writer()
    service = DbWriterService(redis=_fake_redis(pubsub), writer=writer)

    frame = json.dumps(
        {"table": "account_snaps", "payload": _acct_payload()}, default=str
    )
    service._enqueue(frame)

    assert writer.queue.qsize() == 1
    table, payload = writer.queue.get_nowait()
    assert table == "account_snaps"
    assert payload["net_liq_usd"] == 100_000.0
    # After json.dumps(default=str), datetime comes back as an ISO string.
    assert isinstance(payload["timestamp"], str)


@pytest.mark.asyncio
async def test_enqueue_drops_malformed_frame_without_raising(caplog):
    pubsub = _FakePubSub()
    writer = await _build_memory_writer()
    service = DbWriterService(redis=_fake_redis(pubsub), writer=writer)

    service._enqueue("not-json")
    service._enqueue(json.dumps({"missing_table_key": True}))

    assert writer.queue.qsize() == 0  # nothing forwarded


@pytest.mark.asyncio
async def test_end_to_end_batched_insert(monkeypatch):
    """10 frames in, one commit out — writer batches them together."""
    from bus import publisher

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "set_heartbeat", noop)

    pubsub = _FakePubSub()
    writer = await _build_memory_writer(batch_timeout_s=0.05)
    service = DbWriterService(redis=_fake_redis(pubsub), writer=writer)

    # Push 10 events directly onto the writer queue (bypass JSON roundtrip —
    # the JSON frame path is covered by test_enqueue_parses_frame, and SQLite
    # can't round-trip string timestamps back to DateTime columns).
    for i in range(10):
        writer.queue.put_nowait(("account_snaps", _acct_payload(100_000 + i)))

    async def stopper():
        # Give the writer ~300 ms to drain + commit + heartbeat, then stop.
        await asyncio.sleep(0.3)
        service.request_stop()

    await asyncio.gather(service.run(), stopper())

    # The 10 rows landed in the in-memory SQLite via batch insert.
    async with writer.session_factory() as s:
        from sqlalchemy import func, select

        count = (await s.execute(select(func.count()).select_from(AccountHistory))).scalar_one()
        assert count == 10


@pytest.mark.asyncio
async def test_flush_on_stop_commits_buffered_events(monkeypatch):
    """Buffered events at stop-time must hit Postgres before exit."""
    from bus import publisher

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "set_heartbeat", noop)

    pubsub = _FakePubSub()
    writer = await _build_memory_writer(batch_timeout_s=10.0)  # very slow batch timer
    service = DbWriterService(redis=_fake_redis(pubsub), writer=writer)

    # Push events directly — same rationale as test_end_to_end_batched_insert.
    for i in range(3):
        writer.queue.put_nowait(("account_snaps", _acct_payload(200_000 + i)))

    async def stopper():
        await asyncio.sleep(0.1)
        service.request_stop()

    await asyncio.gather(service.run(), stopper())

    async with writer.session_factory() as s:
        from sqlalchemy import func, select

        count = (await s.execute(select(func.count()).select_from(AccountHistory))).scalar_one()
        assert count == 3


@pytest.mark.asyncio
async def test_non_message_frames_are_ignored():
    pubsub = _FakePubSub()
    writer = await _build_memory_writer()
    service = DbWriterService(redis=_fake_redis(pubsub), writer=writer)

    pubsub.feed_non_message()
    pubsub.feed_non_message()

    subscribe_task = asyncio.create_task(service._subscribe_loop())
    await asyncio.sleep(0.1)
    subscribe_task.cancel()
    try:
        await subscribe_task
    except asyncio.CancelledError:
        pass

    assert writer.queue.qsize() == 0
