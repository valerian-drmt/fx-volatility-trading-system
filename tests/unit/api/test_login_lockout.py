"""Per-username login lockout (#296, audit 01#4)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth
from api.auth import LoginLockout, lockout
from api.config import Settings, get_settings
from api.routers.auth import router as auth_router

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_lockout():
    # The per-IP throttle on /login is the other guard; these tests exercise
    # the username lockout alone, so the throttle is off for their duration.
    from api.middleware.rate_limit import limiter

    lockout.reset_all()
    limiter.enabled = False
    yield
    limiter.enabled = True
    lockout.reset_all()


def test_lockout_counter_locks_at_the_threshold_and_expires() -> None:
    lk = LoginLockout()
    t0 = 1_000.0
    for _ in range(4):
        assert lk.record_failure("trader", threshold=5, seconds=900, now=t0) == 0.0
    assert lk.locked_for("trader", now=t0) == 0.0
    assert lk.record_failure("trader", threshold=5, seconds=900, now=t0) == 900.0
    assert lk.locked_for("trader", now=t0 + 10) == pytest.approx(890.0)
    assert lk.locked_for("trader", now=t0 + 901) == 0.0
    assert lk.locked_for("someone-else", now=t0) == 0.0
    lk.reset("trader")
    assert lk.locked_for("trader", now=t0) == 0.0


def test_lockout_tracks_a_bounded_number_of_usernames() -> None:
    lk = LoginLockout()
    for i in range(lk._MAX_TRACKED + 50):
        lk.record_failure(f"u{i}", threshold=5, seconds=900, now=float(i))
    assert len(lk._state) <= lk._MAX_TRACKED


def _client(threshold: int = 3) -> TestClient:
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        AUTH_SECRET="test-secret", AUTH_USERNAME="trader", AUTH_SALT="salt",
        AUTH_PASSWORD_HASH=auth.hash_password("pw", "salt"), AUTH_COOKIE_SECURE=False,
        AUTH_LOCKOUT_THRESHOLD=threshold, AUTH_LOCKOUT_SECONDS=900,
    )
    return TestClient(app)


def test_login_locks_the_username_after_repeated_failures_even_with_the_right_password() -> None:
    c = _client(threshold=3)
    for _ in range(3):
        assert c.post("/api/v1/auth/login", json={"username": "trader", "password": "nope"}).status_code == 401
    r = c.post("/api/v1/auth/login", json={"username": "trader", "password": "pw"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # Another username is not locked by the first one's failures.
    assert c.post("/api/v1/auth/login", json={"username": "other", "password": "pw"}).status_code == 401


def test_a_successful_login_resets_the_counter() -> None:
    c = _client(threshold=3)
    for _ in range(2):
        c.post("/api/v1/auth/login", json={"username": "trader", "password": "nope"})
    assert c.post("/api/v1/auth/login", json={"username": "trader", "password": "pw"}).status_code == 200
    for _ in range(2):
        c.post("/api/v1/auth/login", json={"username": "trader", "password": "nope"})
    assert c.post("/api/v1/auth/login", json={"username": "trader", "password": "pw"}).status_code == 200
