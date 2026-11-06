"""/health carries the build identity; /health/ready asserts real state (#344).

OPS-12: both the container healthcheck and the deploy smoke used to hit a
constant. The readiness probe must go 503 when Postgres or Redis is down, and
/health must say which build answers so the smoke can assert the shipped sha.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.responses import JSONResponse

from api.routers import health


class _Redis:
    def __init__(self, up: bool = True) -> None:
        self.up = up

    async def ping(self) -> bool:
        if not self.up:
            raise ConnectionError("redis down")
        return True

    async def get(self, key: str) -> str | None:
        return datetime.now(UTC).isoformat() if self.up else None


class _Db:
    def __init__(self, up: bool = True) -> None:
        self.up = up

    async def execute(self, _stmt: object) -> None:
        if not self.up:
            raise ConnectionError("pg down")


def test_health_reports_version_and_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc123def456")
    body = health.health()
    assert body["status"] == "OK"
    assert body["git_sha"] == "abc123def456"
    assert body["version"]  # package version or the 0.0.0 fallback, never empty


def test_health_git_sha_unknown_when_not_built_with_the_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert health.health()["git_sha"] == "unknown"


def test_ready_is_200_when_both_dependencies_answer() -> None:
    body = asyncio.run(health.health_ready(_Redis(), _Db()))  # type: ignore[arg-type]
    assert not isinstance(body, JSONResponse)
    assert body["status"] == "OK"
    assert body["components"] == {"redis": "OK", "database": "OK"}
    assert "git_sha" in body


@pytest.mark.parametrize("redis_up, db_up", [(False, True), (True, False), (False, False)])
def test_ready_is_503_when_a_dependency_is_down(redis_up: bool, db_up: bool) -> None:
    resp = asyncio.run(health.health_ready(_Redis(redis_up), _Db(db_up)))  # type: ignore[arg-type]
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 503
    assert b'"status":"DEGRADED"' in resp.body


def test_extended_covers_all_six_engines_with_their_own_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "_backup_state_path", lambda: "")
    body = asyncio.run(health.health_extended(_Redis(), _Db()))  # type: ignore[arg-type]
    engines = body["components"]["engines"]
    assert set(engines) == {
        "market_data", "vol_engine", "risk_engine", "db_writer", "analytics_engine", "execution",
    }
    assert all(v == "OK" for v in engines.values())
    # thresholds mirror docker-compose.yml / the engine Dockerfiles
    assert health.ENGINE_STALE_S["vol_engine"] == 300
    assert health.ENGINE_STALE_S["risk_engine"] == 30
    assert health.ENGINE_STALE_S["market_data"] == 60
