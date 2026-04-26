"""Redis client factories — async for engines/FastAPI, sync for PyQt threads.

Two clients serve two worlds :

    get_redis()      -> redis.asyncio.Redis
        Used by the async engines and the FastAPI app. Non-blocking,
        single process-level ConnectionPool shared between callers.

    get_sync_redis() -> redis.Redis
        Used by PyQt thread-pool callers that cannot await. Same
        URL, separate pool (mixing sync/async clients on the same
        connection object is not safe).

Both read ``REDIS_URL`` lazily on first call and cache the instance
for the rest of the process lifetime. ``reset_clients_for_tests()``
clears the caches when a test monkeypatches the URL.

The pool is sized at ``max_connections=50`` which matches ``09-redis.md``
and is well above the expected steady-state (3 engines + FastAPI ~4
workers = ~10 concurrent consumers).
"""

from __future__ import annotations

import os

import redis
import redis.asyncio as aioredis

_POOL_MAX_CONNECTIONS: int = 50
_ENV_VAR: str = "REDIS_URL"

_async_client: aioredis.Redis | None = None
_sync_client: redis.Redis | None = None


def _redis_url() -> str:
    url = os.environ.get(_ENV_VAR)
    if not url:
        raise RuntimeError(
            f"{_ENV_VAR} is not set. See .env.example for the expected format."
        )
    return url


def get_redis() -> aioredis.Redis:
    """Return the process-level async Redis client (lazy init)."""
    global _async_client
    if _async_client is None:
        pool = aioredis.ConnectionPool.from_url(
            _redis_url(),
            max_connections=_POOL_MAX_CONNECTIONS,
            decode_responses=True,
        )
        _async_client = aioredis.Redis(connection_pool=pool)
    return _async_client


def get_sync_redis() -> redis.Redis:
    """Return the process-level sync Redis client (lazy init).

    For callers stuck in a sync context (PyQt thread-pool tasks that
    cannot be awaited). Do NOT use from an async function — the sync
    client blocks the event loop.
    """
    global _sync_client
    if _sync_client is None:
        pool = redis.ConnectionPool.from_url(
            _redis_url(),
            max_connections=_POOL_MAX_CONNECTIONS,
            decode_responses=True,
        )
        _sync_client = redis.Redis(connection_pool=pool)
    return _sync_client


def reset_clients_for_tests() -> None:
    """Drop both cached clients so the next call reads REDIS_URL again.

    Call from tests that ``monkeypatch.setenv(REDIS_URL, ...)`` after
    the module was already imported.
    """
    global _async_client, _sync_client
    _async_client = None
    _sync_client = None
