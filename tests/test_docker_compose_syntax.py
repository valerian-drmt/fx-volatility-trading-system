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
def test_api_service_present_and_built_locally(compose: dict):
    svc = compose["services"]["api"]
    assert svc["build"]["context"] == "."
    assert svc["build"]["dockerfile"] == "infrastructure/docker/Dockerfile.api"
    assert svc["networks"] == ["fxvol-internal"]
    assert svc.get("restart") == "unless-stopped"
    # Must wait for postgres AND redis healthy before starting — otherwise
    # the async engine factory crashes on the first query.
    deps = svc["depends_on"]
    assert deps["postgres"]["condition"] == "service_healthy"
    assert deps["redis"]["condition"] == "service_healthy"


@pytest.mark.unit
def test_api_uses_internal_dns_not_localhost(compose: dict):
    env = compose["services"]["api"]["environment"]
    # Inside the compose network, postgres and redis are reachable by service
    # name, NOT by localhost — a stale `localhost` URL is a classic mistake
    # that wedges the api on startup.
    assert "@postgres:" in env["DATABASE_URL"]
    assert "redis://redis:" in env["REDIS_URL"]


@pytest.mark.unit
def test_api_has_http_healthcheck(compose: dict):
    hc = compose["services"]["api"]["healthcheck"]
    test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
    assert "/api/v1/health" in test_cmd
    # Start-period buys uvicorn time to bind before failing — without it the
    # container is restarted mid-boot in noisy networks.
    assert hc.get("start_period")


@pytest.mark.unit
def test_dockerfile_api_ships_uvicorn():
    dockerfile = (REPO_ROOT / "infrastructure" / "docker" / "Dockerfile.api").read_text()
    assert "FROM python:3.11-slim" in dockerfile or "${PYTHON_IMAGE}" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "uvicorn" in dockerfile
    assert "COPY api/" in dockerfile
    assert "COPY src/" in dockerfile


@pytest.mark.unit
def test_frontend_service_build_context(compose: dict):
    svc = compose["services"]["frontend"]
    assert svc["build"]["context"] == "."
    assert svc["build"]["dockerfile"] == "infrastructure/docker/Dockerfile.web"
    assert svc["networks"] == ["fxvol-internal"], (
        "frontend container serves the bundle on :8080 — only nginx needs to reach it"
    )


@pytest.mark.unit
def test_nginx_service_exposes_80_and_443(compose: dict):
    svc = compose["services"]["nginx"]
    assert svc["image"] == "nginx:alpine"
    # Public-facing : ports 80/443 mapped to the host.
    port_strings = {str(p) for p in svc["ports"]}
    assert "80:80" in port_strings
    assert "443:443" in port_strings
    # Straddles both public and internal networks — the bridge between
    # the outside world and the application bus.
    assert set(svc["networks"]) == {"fxvol-public", "fxvol-internal"}


@pytest.mark.unit
def test_nginx_mounts_the_reverse_proxy_conf(compose: dict):
    mounts = compose["services"]["nginx"]["volumes"]
    assert any(
        "infrastructure/nginx/nginx-dev.conf" in str(m)
        and "/etc/nginx/conf.d/default.conf" in str(m)
        for m in mounts
    ), "nginx must mount nginx-dev.conf as its default.conf"


@pytest.mark.unit
def test_nginx_waits_for_api_and_frontend_healthy(compose: dict):
    deps = compose["services"]["nginx"]["depends_on"]
    assert deps["api"]["condition"] == "service_healthy"
    assert deps["frontend"]["condition"] == "service_healthy"


@pytest.mark.unit
def test_nginx_conf_routes_api_and_ws_to_api_service():
    """The reverse-proxy conf must route /api/ and /ws/ to the api service
    by compose DNS name — pointing at localhost or 127.0.0.1 would hit
    nginx itself and break the whole stack."""
    conf = (REPO_ROOT / "infrastructure" / "nginx" / "nginx-dev.conf").read_text()
    assert "api:8000" in conf
    assert "frontend:8080" in conf
    assert "location /ws/" in conf
    assert "Upgrade $http_upgrade" in conf


@pytest.mark.unit
def test_env_example_declares_r6_variables():
    env = (REPO_ROOT / ".env.example").read_text()
    assert "DB_PASSWORD=" in env
    assert "VNC_PASSWORD=" in env, ".env.example must document VNC_PASSWORD for PR #4"
    assert "IB_USERID=" in env
    assert "IB_PASSWORD=" in env
