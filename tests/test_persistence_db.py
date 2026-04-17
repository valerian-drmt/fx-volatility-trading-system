"""Unit tests for the persistence async engine / session factory."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from persistence import db


@pytest.fixture(autouse=True)
def _reset_cached_engine():
    db.reset_engine_for_tests()
    yield
    db.reset_engine_for_tests()


def test_get_engine_uses_database_url_env_var(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pw@host:5432/dbname",
    )
    engine = db.get_engine()
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.username == "user"
    assert engine.url.host == "host"
    assert engine.url.port == 5432
    assert engine.url.database == "dbname"


def test_get_engine_raises_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        db.get_engine()


def test_get_engine_is_cached(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d"
    )
    first = db.get_engine()
    second = db.get_engine()
    assert first is second


def test_get_sessionmaker_returns_async_sessionmaker(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d"
    )
    maker = db.get_sessionmaker()
    assert isinstance(maker, async_sessionmaker)
    session = maker()
    assert isinstance(session, AsyncSession)


def test_reset_engine_for_tests_clears_cache(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/a"
    )
    engine_a = db.get_engine()

    db.reset_engine_for_tests()
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/b"
    )
    engine_b = db.get_engine()

    assert engine_a is not engine_b
    assert engine_b.url.database == "b"
