"""Async SQLAlchemy engine and session factory.

Reads ``DATABASE_URL`` from the environment on first access. The engine is
created lazily and cached for the process lifetime so tests can override the
URL via ``monkeypatch`` before the first call.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. See .env.example for the expected format."
        )
    return url


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _database_url(),
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


def reset_engine_for_tests() -> None:
    """Clear the cached engine and sessionmaker. Call from tests that mutate DATABASE_URL."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
