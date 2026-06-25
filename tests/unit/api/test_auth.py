"""Unit tests for the single-trader auth boundary."""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api import auth
from api.auth import require_write
from api.config import Settings, get_settings
from api.routers.auth import router as auth_api_router

pytestmark = pytest.mark.unit

SECRET = "test-secret"


def test_token_roundtrip_valid():
    tok = auth.issue_token("trader", SECRET, ttl_s=60)
    claims = auth.verify_token(tok, SECRET)
    assert claims is not None and claims["sub"] == "trader"


def test_token_rejected_when_tampered_or_wrong_secret():
    tok = auth.issue_token("trader", SECRET, ttl_s=60)
    assert auth.verify_token(tok, "other-secret") is None
    assert auth.verify_token(tok + "x", SECRET) is None
    assert auth.verify_token("garbage", SECRET) is None


def test_token_rejected_when_expired():
    tok = auth.issue_token("trader", SECRET, ttl_s=-1)  # already expired
    assert auth.verify_token(tok, SECRET) is None


def test_password_verify_constant_time_and_empty_hash_fails():
    h = auth.hash_password("hunter2", "salt")
    assert auth.verify_password("hunter2", "salt", h) is True
    assert auth.verify_password("wrong", "salt", h) is False
    # Unprovisioned (empty hash) must never authenticate.
    assert auth.verify_password("anything", "salt", "") is False


def _client(pw_hash: str) -> TestClient:
    app = FastAPI()
    app.include_router(auth_api_router)

    @app.post("/api/v1/_protected")
    def _protected(_claims: dict = Depends(require_write)) -> dict:
        return {"ok": True}

    # Sandbox config: auth_* are read-only lowercase views over UPPERCASE env
    # fields (case_sensitive). Construct with the UPPERCASE field names.
    app.dependency_overrides[get_settings] = lambda: Settings(
        AUTH_SECRET=SECRET,
        AUTH_USERNAME="trader",
        AUTH_SALT="salt",
        AUTH_PASSWORD_HASH=pw_hash,
        AUTH_COOKIE_SECURE=False,  # TestClient is http
    )
    return TestClient(app)


def test_require_write_401_without_cookie():
    client = _client(auth.hash_password("pw", "salt"))
    assert client.post("/api/v1/_protected").status_code == 401


def test_login_sets_cookie_then_protected_route_passes():
    client = _client(auth.hash_password("pw", "salt"))
    bad = client.post("/api/v1/auth/login", json={"username": "trader", "password": "nope"})
    assert bad.status_code == 401

    ok = client.post("/api/v1/auth/login", json={"username": "trader", "password": "pw"})
    assert ok.status_code == 200 and ok.json() == {"authenticated": True}
    # Cookie now set on the client → protected route + /me pass.
    assert client.post("/api/v1/_protected").status_code == 200
    assert client.get("/api/v1/auth/me").json() == {"authenticated": True}

    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").json() == {"authenticated": False}


def test_login_fails_when_no_credentials_provisioned():
    client = _client("")  # empty hash = unprovisioned
    r = client.post("/api/v1/auth/login", json={"username": "trader", "password": "pw"})
    assert r.status_code == 401
