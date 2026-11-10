"""Async SQLAlchemy engine and session factory.

Reads ``DATABASE_URL`` from the environment on first access. The engine is
created lazily and cached for the process lifetime so tests can override the
URL via ``monkeypatch`` before the first call.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str:
    from shared.config import get_settings

    url = get_settings().DATABASE_URL
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. See .env.example for the expected format."
        )
    return url


def _statement_timeout_ms() -> int:
    """``DB_STATEMENT_TIMEOUT_MS`` — server-side cap on a single statement.

    0 / unset = no cap (engines : batch inserts, daily prunes, alembic). The
    API sets it (docker-compose) so one pathological query cannot pile up
    across UI polls until the pool is exhausted — the request fails fast with
    a 500 instead of taking every other endpoint down with it.
    """
    from shared.config import get_settings

    return get_settings().db_statement_timeout_ms


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        timeout_ms = _statement_timeout_ms()
        if timeout_ms and _database_url().startswith("postgresql"):
            kwargs["connect_args"] = {
                "server_settings": {"statement_timeout": str(timeout_ms)},
            }
        _engine = create_async_engine(_database_url(), **kwargs)
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
