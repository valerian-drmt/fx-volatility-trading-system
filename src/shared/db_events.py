"""Publisher helper : engines push DB events onto a Redis channel, the
db-writer subscribes and batches them into Postgres.

The "queue" between engines and Postgres is a Redis pub/sub channel
(engines run in separate containers). Payload shape : ``(table_name,
row_dict)`` so ``db_writer`` can keep its bulk-insert logic unchanged.
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
