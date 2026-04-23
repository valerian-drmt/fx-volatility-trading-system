"""Async Redis client factory for R7 service containers.

Delegates to the existing ``bus.redis_client`` implementation (R3 PR #2)
to avoid duplicating the pool management logic. The purpose of this
module is to give the R7 ``services/<name>/`` code a stable import path
that won't change when the legacy ``src/bus/`` package is eventually
retired : services import from ``shared.redis_client``, not from
``bus.redis_client`` directly.
"""
from __future__ import annotations

from redis import asyncio as aioredis

from bus import redis_client as _bus_redis


def get_async_redis() -> aioredis.Redis:
    """Return the process-wide async Redis client (cached on first call)."""
    return _bus_redis.get_redis()


def reset_for_tests() -> None:
    """Drop the cached client so a test can monkeypatch ``REDIS_URL``."""
    _bus_redis.reset_clients_for_tests()
