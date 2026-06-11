"""Tests for /api/v1/admin/config via FastAPI TestClient (sqlite + stubbed redis)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.dependencies import get_db_session, get_redis
from api.main import app
from persistence.models import Base


@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
def client(test_db):
    async def _session():
        async with test_db() as s:
            yield s

    redis_stub = AsyncMock()
    redis_stub.publish = AsyncMock(return_value=1)

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_redis] = lambda: redis_stub
    yield TestClient(app), redis_stub
    app.dependency_overrides.clear()


def test_get_current_returns_defaults_on_empty_db(client):
    tc, _ = client
    r = tc.get("/api/v1/admin/config")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 0
    assert body["config"]["signal"]["z_threshold_arm"] == 1.5


def test_schema_endpoint_returns_pydantic_json_schema(client):
    tc, _ = client
    r = tc.get("/api/v1/admin/config/schema")
    assert r.status_code == 200
    schema = r.json()
    assert "properties" in schema
    assert "signal" in schema["properties"]


def test_put_creates_new_version_and_publishes(client):
    tc, redis = client
    r = tc.put("/api/v1/admin/config", json={
        "patch": {"signal": {"z_threshold_arm": 1.0}},
        "user": "valerian", "comment": "looser arm",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["config"]["signal"]["z_threshold_arm"] == 1.0
    redis.publish.assert_awaited_once()
    assert redis.publish.await_args.args[0] == "config:changed"


def test_put_rejects_out_of_bounds_with_422(client):
    tc, _ = client
    r = tc.put("/api/v1/admin/config", json={
        "patch": {"signal": {"z_threshold_arm": 999.0}},
    })
    assert r.status_code == 422
    assert "schema" in r.json()["detail"]["message"]


def test_history_endpoint(client):
    tc, _ = client
    for n in range(1, 4):
        tc.put("/api/v1/admin/config", json={
            "patch": {"sizing": {"base_size": 10 + n}},
        })
    r = tc.get("/api/v1/admin/config/history?limit=10")
    assert r.status_code == 200
    versions = [row["version"] for row in r.json()]
    assert versions == [3, 2, 1]


def test_revert_creates_new_version_pointing_at_target(client):
    tc, _ = client
    tc.put("/api/v1/admin/config", json={"patch": {"sizing": {"base_size": 50}}})
    tc.put("/api/v1/admin/config", json={"patch": {"sizing": {"base_size": 99}}})

    r = tc.post("/api/v1/admin/config/revert/1", json={"user": "valerian"})
    assert r.status_code == 200
    assert r.json()["version"] == 3
    assert r.json()["config"]["sizing"]["base_size"] == 50


def test_revert_unknown_version_returns_404(client):
    tc, _ = client
    r = tc.post("/api/v1/admin/config/revert/42", json={})
    assert r.status_code == 404
    assert "42 not found" in r.json()["detail"]
