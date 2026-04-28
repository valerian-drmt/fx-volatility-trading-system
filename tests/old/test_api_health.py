"""Tests for /api/v1/health and /api/v1/health/extended."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


@pytest.fixture
def client_with_mocks():
    """TestClient with mocked Redis + DB session so endpoints are testable offline."""
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()
    fake_redis.ping = AsyncMock(return_value=True)

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    async def fake_sessionmaker_factory():
        """Stand-in for the async context manager yielded by get_db_session."""
        class _Ctx:
            async def __aenter__(self): return fake_session
            async def __aexit__(self, *a): return None
        return _Ctx()

    with patch("api.main.aioredis.from_url", return_value=fake_redis), \
         patch("api.dependencies.get_sessionmaker",
               return_value=lambda: fake_sessionmaker_factory().__aenter__.__self__) as _:
        # Simpler : override dep directly.
        app = create_app()

        async def _fake_db():
            yield fake_session
        from api.dependencies import get_db_session
        app.dependency_overrides[get_db_session] = _fake_db

        with TestClient(app) as c:
            yield c, fake_redis


@pytest.mark.unit
class TestHealthBasic:
    def test_health_returns_ok(self, client_with_mocks):
        client, _ = client_with_mocks
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json() == {"status": "OK"}

    def test_health_appears_in_openapi_schema(self, client_with_mocks):
        client, _ = client_with_mocks
        schema = client.get("/openapi.json").json()
        assert "/api/v1/health" in schema["paths"]


@pytest.mark.unit
class TestHealthExtended:
    def test_all_systems_ok_when_heartbeats_fresh(self, client_with_mocks):
        client, fake_redis = client_with_mocks
        now = _iso(datetime.now(UTC))
        fake_redis.get = AsyncMock(return_value=now)

        r = client.get("/api/v1/health/extended")
        body = r.json()
        assert r.status_code == 200
        assert body["status"] == "OK"
        assert body["components"] == {
            "redis": "OK",
            "database": "OK",
            "engines": {"market_data": "OK", "vol_engine": "OK", "risk_engine": "OK"},
        }

    def test_stale_heartbeat_downgrades_engine(self, client_with_mocks):
        client, fake_redis = client_with_mocks
        stale_ts = _iso(datetime.now(UTC) - timedelta(seconds=120))
        fake_redis.get = AsyncMock(return_value=stale_ts)

        r = client.get("/api/v1/health/extended")
        body = r.json()
        assert body["status"] == "DEGRADED"
        assert body["components"]["engines"]["market_data"].startswith("STALE")

    def test_missing_heartbeat_is_down(self, client_with_mocks):
        client, fake_redis = client_with_mocks
        fake_redis.get = AsyncMock(return_value=None)

        r = client.get("/api/v1/health/extended")
        body = r.json()
        assert body["status"] == "DEGRADED"
        assert all(v == "DOWN" for v in body["components"]["engines"].values())

    def test_redis_ping_failure_marks_redis_down(self, client_with_mocks):
        client, fake_redis = client_with_mocks
        fake_redis.ping = AsyncMock(side_effect=ConnectionError("redis down"))
        fake_redis.get = AsyncMock(return_value=_iso(datetime.now(UTC)))

        r = client.get("/api/v1/health/extended")
        body = r.json()
        assert body["components"]["redis"] == "DOWN"
        assert body["status"] == "DEGRADED"
