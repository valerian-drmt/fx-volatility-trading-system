"""Module-level Redis client for execution-engine event handlers.

Set by ``main.lifespan`` at startup ; read by fills / hedge / rollback
handlers which fire from ib_insync callbacks (no FastAPI request context).
``None`` when Redis is not configured ; publishers no-op silently.
"""
from __future__ import annotations

from redis import asyncio as aioredis

_client: aioredis.Redis | None = None


def set_client(client: aioredis.Redis | None) -> None:
    global _client
    _client = client


def get_client() -> aioredis.Redis | None:
    return _client
