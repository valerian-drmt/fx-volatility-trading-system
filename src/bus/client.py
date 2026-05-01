"""Process-wide Redis client factories — async and sync.

Two clients serve two worlds :

    get_async_redis() -> redis.asyncio.Redis
        For engines, FastAPI, and any async consumer. Single
        process-level ConnectionPool shared between callers.

    get_sync_redis()  -> redis.Redis
        For sync callers (Jupyter smoke notebooks, scripts).
        Same URL, separate pool — mixing sync/async clients on the
        same connection object is not safe.

Both read ``REDIS_URL`` lazily on first call and cache the instance for
the rest of the process lifetime. ``reset_clients_for_tests()`` clears
the caches when a test monkeypatches the URL.

Pool size : ``max_connections=50``, well above the steady-state load
(5 engines + ~4 FastAPI workers ≈ 10 concurrent consumers).
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


def get_async_redis() -> aioredis.Redis:
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

    Do NOT use from an async function — the sync client blocks the
    event loop. Reserved for Jupyter notebooks and one-shot scripts.
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
    """Drop both cached clients so the next call reads REDIS_URL again."""
    global _async_client, _sync_client
    _async_client = None
    _sync_client = None


# Back-compat alias — historical name used by bus.publisher and a smoke
# notebook. New code should call get_async_redis() directly.
get_redis = get_async_redis
