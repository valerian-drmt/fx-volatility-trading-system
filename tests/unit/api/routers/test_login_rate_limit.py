"""Unit tests for the /login brute-force throttle (slowapi, 5/min per IP)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.middleware.rate_limit import limiter

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


def test_login_throttled_after_five_attempts():
    client = TestClient(app)
    body = {"username": "trader", "password": "wrong-password"}
    for _ in range(5):
        resp = client.post("/api/v1/auth/login", json=body)
        assert resp.status_code == 401
    resp = client.post("/api/v1/auth/login", json=body)
    assert resp.status_code == 429
    assert "Rate limit exceeded" in resp.json()["detail"]


def test_limiter_middleware_registered():
    # The limiter is inert without SlowAPIMiddleware — fail closed on a
    # future refactor dropping it.
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "SlowAPIMiddleware" in names
