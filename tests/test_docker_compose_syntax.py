"""Parse-level validation of the production-like ``docker-compose.yml``.

R6 PR #1 ships postgres + redis + the three bridge networks. Every
downstream PR extends this file (api → PR #2, frontend+nginx → PR #3,
ib-gateway → PR #4, healthchecks → PR #5) ; the assertions here evolve
with it so a missing service or a mistyped network is caught before
``docker compose up``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"

EXPECTED_NETWORKS = {"fxvol-public", "fxvol-internal", "fxvol-external"}
EXPECTED_VOLUMES = {"postgres_data", "redis_data"}


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE.exists(), f"missing {COMPOSE}"
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_compose_parses(compose: dict):
    assert isinstance(compose, dict)
    assert {"services", "networks", "volumes"} <= set(compose)


@pytest.mark.unit
def test_expected_networks_declared(compose: dict):
    assert set(compose["networks"]) == EXPECTED_NETWORKS, (
        f"networks top-level must match exactly {EXPECTED_NETWORKS}"
    )


@pytest.mark.unit
def test_expected_volumes_declared(compose: dict):
    assert set(compose["volumes"]) >= EXPECTED_VOLUMES


@pytest.mark.unit
def test_postgres_service_present_and_internal_only(compose: dict):
    svc = compose["services"]["postgres"]
    assert svc["image"] == "postgres:16-alpine"
    assert svc["networks"] == ["fxvol-internal"], (
        "postgres must stay off the public network — only nginx faces the internet"
    )
    # The restart policy keeps prod-like behaviour under crash.
    assert svc.get("restart") == "unless-stopped"
    # DB_PASSWORD must be required (no silent default) — the compose uses the
    # `${DB_PASSWORD:?…}` syntax which surfaces as a raw string here.
    assert "DB_PASSWORD:?" in svc["environment"]["POSTGRES_PASSWORD"]


@pytest.mark.unit
def test_postgres_mounts_init_sql(compose: dict):
    mounts = compose["services"]["postgres"]["volumes"]
    assert any(
        "infrastructure/postgres/init.sql" in str(m) for m in mounts
    ), "postgres should mount infrastructure/postgres/init.sql for first-boot extensions"


@pytest.mark.unit
def test_redis_service_present_and_internal_only(compose: dict):
    svc = compose["services"]["redis"]
    assert svc["image"] == "redis:7-alpine"
    assert svc["networks"] == ["fxvol-internal"]
    assert any(
        "infrastructure/redis/redis.conf" in str(v) for v in svc["volumes"]
    ), "redis must mount the hardened redis.conf"


@pytest.mark.unit
def test_no_host_port_exposure_on_internal_services(compose: dict):
    """Prod-like compose keeps postgres/redis off host ports — only nginx
    exposes 80/443 (PR #3). The dev-quick-iter compose (docker-compose.dev.yml)
    is the right place for host-port mappings."""
    for name in ("postgres", "redis"):
        assert "ports" not in compose["services"][name], (
            f"{name} must not expose ports in docker-compose.yml — use "
            "docker-compose.dev.yml for host-port mappings"
        )


@pytest.mark.unit
def test_init_sql_declares_extensions():
    init_sql = (REPO_ROOT / "infrastructure" / "postgres" / "init.sql").read_text()
    assert 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"' in init_sql
    assert "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" in init_sql


@pytest.mark.unit
def test_env_example_declares_r6_variables():
    env = (REPO_ROOT / ".env.example").read_text()
    assert "DB_PASSWORD=" in env
    assert "VNC_PASSWORD=" in env, ".env.example must document VNC_PASSWORD for PR #4"
    assert "IB_USERID=" in env
    assert "IB_PASSWORD=" in env
