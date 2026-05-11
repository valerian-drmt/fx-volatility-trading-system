"""Redis → Postgres bridge. Subscribes to the ``db_events`` channel,
hands every frame to the existing ``AsyncDatabaseWriter`` queue, and
flushes cleanly on SIGTERM.

The batching + retry logic lives in ``persistence.writer`` (R2) — this
service only owns the Redis subscribe loop and the graceful-shutdown
sequence. Replaces the in-process ``asyncio.Queue`` that the R2
monolith used to bridge engines and Postgres.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from bus import keys, publisher
from persistence.writer import AsyncDatabaseWriter
from shared.db_events import DB_EVENTS_CHANNEL

HEARTBEAT_INTERVAL_S = 5.0

logger = logging.getLogger(__name__)


class _RedisLike(Protocol):
    def pubsub(self) -> Any: ...


class DbWriterService:
    """Long-running service : subscribe Redis + forward to the writer queue."""

    def __init__(
        self,
        *,
        redis: _RedisLike,
        writer: AsyncDatabaseWriter,
        channel: str = DB_EVENTS_CHANNEL,
    ) -> None:
        self.redis = redis
        self.writer = writer
        self.channel = channel
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Subscribe, enqueue, run the writer loop concurrently, drain on stop."""
        writer_task = asyncio.create_task(self.writer.run(), name="async_db_writer")
        subscriber_task = asyncio.create_task(self._subscribe_loop(), name="db_events_subscriber")
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="db_writer_heartbeat")

        try:
            # Block until request_stop() is called (SIGTERM / SIGINT from main).
            await self._stop.wait()
        finally:
            # Order matters : stop accepting new events first, then flush.
            subscriber_task.cancel()
            heartbeat_task.cancel()
            for t in (subscriber_task, heartbeat_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            await self.writer.shutdown()
            try:
                await writer_task
            except Exception:
                logger.exception("writer_task_exited_with_error")

    async def _heartbeat_loop(self) -> None:
        """Fire a Redis heartbeat every HEARTBEAT_INTERVAL_S seconds."""
        from shared.observability import observed_cycle

        while not self._stop.is_set():
            # P0 obs : one heartbeat tick = one cycle for the db-writer
            # engine. Granularity isn't per-batch (which would mix with the
            # shared persistence.writer lib used by api too), but per liveness
            # ping — sufficient to detect a hung service via the
            # engine_last_cycle_timestamp_seconds gauge.
            with observed_cycle("db_writer"):
                try:
                    await publisher.set_heartbeat(self.redis, keys.ENGINE_DB_WRITER)
                except Exception:
                    logger.warning("heartbeat_publish_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_INTERVAL_S)
                break
            except TimeoutError:
                continue

    async def _subscribe_loop(self) -> None:
        """Reconnect-on-drop Redis subscriber. Each frame → writer queue."""
        while not self._stop.is_set():
            pubsub = self.redis.pubsub()
            try:
                await pubsub.subscribe(self.channel)
                logger.info("db_events_subscribed", extra={"channel": self.channel})
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    self._enqueue(message.get("data"))
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError, TimeoutError):
                logger.warning("db_events_subscriber_disconnected, retrying")
                await asyncio.sleep(2)
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass

    def _enqueue(self, raw: Any) -> None:
        """Parse a JSON frame and push (table, payload) onto the writer queue."""
        if raw is None:
            return
        try:
            frame = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            table = frame["table"]
            payload = frame["payload"]
        except (ValueError, TypeError, KeyError):
            logger.warning("db_events_malformed_frame", extra={"raw": str(raw)[:120]})
            return
        payload = _coerce_datetime_fields(payload)
        try:
            self.writer.queue.put_nowait((table, payload))
        except asyncio.QueueFull:
            logger.warning("writer_queue_full_event_dropped", extra={"table": table})


_DATETIME_FIELDS: tuple[str, ...] = ("timestamp", "opened_at", "closed_at", "ts")


def _coerce_datetime_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert ISO-8601 strings on known datetime fields back to ``datetime``.

    publish_db_event serialises datetimes via json.dumps(default=str) →
    ISO strings. asyncpg refuses strings on ``DateTime(timezone=True)``
    columns (\"expected datetime.datetime instance\"), so we round-trip
    the known-named fields here. Unknown fields are left untouched.
    """
    from datetime import datetime

    out = dict(payload)
    for key in _DATETIME_FIELDS:
        value = out.get(key)
        if not isinstance(value, str):
            continue
        # Accept both `+00:00` and the trailing-Z form produced by R9 engines.
        iso = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            out[key] = datetime.fromisoformat(iso)
        except ValueError:
            logger.warning("db_events_bad_datetime", extra={"key": key, "value": value[:40]})
    return out
