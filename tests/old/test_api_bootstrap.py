"""FastAPI scaffold bootstrap tests — no Redis, no DB, just app wiring."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture
def client():
    """TestClient that patches out the Redis connection so startup succeeds."""
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()
    with patch("api.main.aioredis.from_url", return_value=fake_redis), \
         TestClient(create_app()) as c:
        yield c


@pytest.mark.unit
class TestAppBootstrap:
    def test_openapi_schema_available(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        assert r.json()["info"]["title"] == "FXVol API"

    def test_docs_endpoint_available(self, client):
        assert client.get("/docs").status_code == 200

    def test_metrics_endpoint_returns_prometheus_text(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        # /metrics body contains at least the timing histogram name.
        assert "fxvol_http_request_duration_seconds" in r.text

    def test_cors_preflight_allowed_origin(self, client):
        r = client.options(
            "/openapi.json",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.unit
class TestSettings:
    def test_default_settings_cover_all_fields(self):
        from api.config import get_settings
        s = get_settings()
        assert s.database_url.startswith("postgresql+asyncpg://")
        assert s.redis_url.startswith("redis://")
        assert s.rate_limit_per_minute == 100
        assert "http://localhost:3000" in s.cors_origins
