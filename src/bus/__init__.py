"""Redis bus — cache + pub/sub for cross-process live state.

The ``bus`` package is the single entry point for every service that
reads or writes to Redis : engines push live state, FastAPI reads it
for REST endpoints, WebSocket bridges re-publish pub/sub events.

Public surface :
    - ``get_redis()``      : async Redis client (redis.asyncio)
    - ``get_sync_redis()`` : sync Redis client for the PyQt thread pool
    - ``keys``             : key name templates (e.g. ``LATEST_SPOT``)
    - ``channels``         : pub/sub channel name constants

Reference : releases/architecture_finale_project/09-redis.md
"""

from __future__ import annotations

from bus.redis_client import get_redis, get_sync_redis

__all__ = ["get_redis", "get_sync_redis"]
