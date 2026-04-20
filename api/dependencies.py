"""FastAPI dependencies — inject Redis client + async DB session."""
from __future__ import annotations

from collections.abc import AsyncIterator

from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.db import get_sessionmaker

# Module-level singleton populated by main.lifespan startup.
_redis_client: aioredis.Redis | None = None


def set_redis_client(client: aioredis.Redis) -> None:
    """Called once at FastAPI startup by lifespan."""
    global _redis_client
    _redis_client = client


def get_redis() -> aioredis.Redis:
    """Raise early if the app tried to use Redis before lifespan ran."""
    if _redis_client is None:
        raise RuntimeError("Redis client not initialized — lifespan not started")
    return _redis_client


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an async DB session inside a transaction (commit on exit, rollback on error)."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
