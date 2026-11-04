"""``DB_STATEMENT_TIMEOUT_MS`` → asyncpg ``server_settings.statement_timeout``.

The api sets it (docker-compose) so a pathological read fails fast instead of
piling up across UI polls until the pool is exhausted (prod 2026-11-04).
Engines leave it unset → no ``connect_args`` at all (batch inserts, daily
prunes and alembic must never be capped).
"""
from __future__ import annotations

import pytest

from persistence import db


@pytest.fixture(autouse=True)
def _fresh_engine(monkeypatch):
    db.reset_engine_for_tests()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/x")
    yield
    db.reset_engine_for_tests()


def _capture_engine_kwargs(monkeypatch):
    seen: dict = {}

    def _fake_create(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(db, "create_async_engine", _fake_create)
    return seen


@pytest.mark.parametrize("raw", ["", "0", "abc", "-5"])
def test_unset_or_invalid_timeout_adds_no_connect_args(monkeypatch, raw):
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", raw)
    seen = _capture_engine_kwargs(monkeypatch)
    db.get_engine()
    assert "connect_args" not in seen
    assert seen["pool_pre_ping"] is True


def test_positive_timeout_is_passed_as_server_setting(monkeypatch):
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "20000")
    seen = _capture_engine_kwargs(monkeypatch)
    db.get_engine()
    assert seen["connect_args"] == {"server_settings": {"statement_timeout": "20000"}}


def test_timeout_ignored_for_non_postgres_urls(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "20000")
    seen = _capture_engine_kwargs(monkeypatch)
    db.get_engine()
    assert "connect_args" not in seen
