"""The api -> execution-engine shared secret (#300, audit 01#1)."""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from shared import internal_auth
from shared.internal_auth import INTERNAL_TOKEN_ENV, INTERNAL_TOKEN_HEADER, internal_headers, token_matches


def test_token_matches_is_false_without_a_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(INTERNAL_TOKEN_ENV, raising=False)
    assert token_matches("anything") is False
    assert token_matches(None) is False
    assert INTERNAL_TOKEN_HEADER not in internal_headers()


def test_token_matches_exactly_and_headers_carry_it_with_the_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "s3cret-token")
    assert token_matches("s3cret-token") is True
    assert token_matches("s3cret-token ") is False and token_matches("S3CRET-TOKEN") is False
    from shared.trace import bind_trace_id, clear_trace_id

    bind_trace_id("abc123def4567890")
    try:
        h = internal_headers()
    finally:
        clear_trace_id()
    assert h[INTERNAL_TOKEN_HEADER] == "s3cret-token"
    assert any(k.lower().startswith("x-") and "trace" in k.lower() for k in h), "trace headers still travel"


def _gated_app() -> TestClient:
    from engines.execution.main import require_internal_token

    app = FastAPI()

    @app.get("/internal/ping", dependencies=[Depends(require_internal_token)])
    def _ping() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_engine_refuses_internal_routes_without_a_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(INTERNAL_TOKEN_ENV, raising=False)
    r = _gated_app().get("/internal/ping", headers={INTERNAL_TOKEN_HEADER: "whatever"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "internal_token_unconfigured"


def test_engine_refuses_a_missing_or_wrong_token_and_accepts_the_right_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "s3cret-token")
    c = _gated_app()
    assert c.get("/internal/ping").status_code == 403
    assert c.get("/internal/ping", headers={INTERNAL_TOKEN_HEADER: "nope"}).status_code == 403
    assert c.get("/internal/ping", headers={INTERNAL_TOKEN_HEADER: "s3cret-token"}).json() == {"ok": True}


def test_every_internal_route_of_the_engine_carries_the_dependency() -> None:
    from engines.execution.main import app, require_internal_token

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/internal/"):
            continue
        deps = [d.call for d in getattr(route, "dependant").dependencies]  # noqa: B009
        assert require_internal_token in deps, path


def test_token_env_name_is_what_compose_and_deploy_render() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    deploy = (root / "infrastructure" / "ec2" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert compose.count(f"{internal_auth.INTERNAL_TOKEN_ENV}: ${{{internal_auth.INTERNAL_TOKEN_ENV}:-") == 2, "api and execution-engine"
    assert f"{internal_auth.INTERNAL_TOKEN_ENV}=" in deploy
