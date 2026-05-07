"""Publisher helper : engines push DB events onto a Redis channel, the
db-writer subscribes and batches them into Postgres.

R2 routed DB writes through an in-process ``asyncio.Queue``. R7 breaks
that up — engines run in separate containers, so the "queue" is now a
Redis pub/sub channel. Payload shape is unchanged (table name + row
dict) so ``db_writer`` can keep the R2 bulk-insert logic verbatim.
"""
from __future__ import annotations

import json
from typing import Any

from redis import asyncio as aioredis

DB_EVENTS_CHANNEL = "db_events"


async def publish_db_event(redis: aioredis.Redis, table: str, payload: dict[str, Any]) -> int:
    """Publish a single ``{"table": …, "payload": …}`` JSON frame.

    Returns the number of subscribers that received the message — useful
    in dev to detect "no db_writer running" silently. The caller should
    log a warning when the return is 0 for more than a handful of cycles.
    """
    frame = json.dumps({"table": table, "payload": payload}, default=str)
    return await redis.publish(DB_EVENTS_CHANNEL, frame)
