"""Live-up integration test for the production-like compose stack.

Gated by ``COMPOSE_RUN_INTEGRATION=1`` because the test spins up the
whole stack (postgres + redis + api + frontend + nginx, optionally
ib-gateway if credentials are present) and waits ~90 seconds for
everything to become healthy. Only run it locally or in a dedicated
CI job with Docker + the required env vars.

Usage:
    # Load all 5 secrets from AWS SSM first (R9 commit #3) :
    .\\scripts\\load_secrets.ps1
    $env:COMPOSE_RUN_INTEGRATION = "1"
    python -m pytest tests/test_compose_up.py -v
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UP_SCRIPT = REPO_ROOT / "scripts" / "up.sh"
DOWN_SCRIPT = REPO_ROOT / "scripts" / "down.sh"

REQUIRED_HEALTHY = {"fxvol-postgres", "fxvol-redis", "fxvol-api", "fxvol-frontend", "fxvol-nginx"}

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPOSE_RUN_INTEGRATION") != "1",
    reason="Set COMPOSE_RUN_INTEGRATION=1 to run the full-stack integration test.",
)


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, capturing output for the test diagnosis on failure."""
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False, **kwargs
    )


def _wait_healthy(timeout: int = 120) -> dict[str, str]:
    """Poll `docker inspect` until every REQUIRED_HEALTHY container is healthy."""
    deadline = time.time() + timeout
    status: dict[str, str] = {}
    while time.time() < deadline:
        status.clear()
        for name in REQUIRED_HEALTHY:
            r = _run(["docker", "inspect", "-f", "{{.State.Health.Status}}", name])
            status[name] = r.stdout.strip() or "missing"
        if all(v == "healthy" for v in status.values()):
            return status
        time.sleep(3)
    return status


def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


@pytest.fixture(scope="module", autouse=True)
def _stack():
    """Start the stack once for the module, tear it down at the end."""
    os.environ.setdefault("DB_PASSWORD", "fxvol")
    os.environ.setdefault("VNC_PASSWORD", "vncpass")
    # Use docker compose directly rather than the bash script — lets Windows
    # runners participate without a bash install. The script is still the
    # canonical entrypoint for humans.
    up = _run(["docker", "compose", "up", "-d", "--build"])
    assert up.returncode == 0, f"docker compose up failed:\n{up.stderr}"
    # Wait for api to become healthy then apply migrations — mirrors what
    # scripts/up.sh does for humans. Tests run as soon as the stack is
    # ready rather than reimplementing each step inline.
    _wait_healthy(timeout=120)
    _run([
        "docker", "compose", "exec", "-T", "api",
        "python", "-m", "alembic", "-c", "persistence/alembic.ini", "upgrade", "head",
    ])
    yield
    _run(["docker", "compose", "down", "--remove-orphans"])


def test_compose_up_all_healthy():
    status = _wait_healthy(timeout=120)
    unhealthy = {k: v for k, v in status.items() if v != "healthy"}
    assert not unhealthy, f"containers not healthy in time: {json.dumps(unhealthy, indent=2)}"


def test_frontend_bundle_served_through_nginx():
    assert _tcp_probe("127.0.0.1", 80), "nginx port 80 not reachable on host"
    r = _run(
        ["docker", "run", "--rm", "--network", "host", "curlimages/curl:8.8.0",
         "-sf", "http://127.0.0.1/"]
    )
    # Fallback curl via a container — some hosts don't ship curl natively.
    if r.returncode != 0:
        # Try the local curl as last resort (Windows Git Bash ships it).
        r = _run(["curl", "-sf", "http://127.0.0.1/"])
    assert r.returncode == 0, f"GET / via nginx failed: {r.stderr}"
    body = r.stdout
    assert '<div id="root">' in body, "expected Vite React mount point in index.html"
    assert "/assets/" in body, "expected hashed asset reference in index.html"


def test_api_reachable_through_nginx():
    r = _run(["curl", "-sf", "http://127.0.0.1/api/v1/health"])
    assert r.returncode == 0, f"GET /api/v1/health failed: {r.stderr}"
    assert '"status":"OK"' in r.stdout


def test_alembic_migrations_apply_in_compose():
    r = _run([
        "docker", "compose", "exec", "-T", "api",
        "python", "-m", "alembic", "-c", "persistence/alembic.ini", "current",
    ])
    assert r.returncode == 0, f"alembic current failed:\n{r.stderr}"
    # When at head, the alembic output includes " (head)".
    assert "(head)" in r.stdout, f"expected Alembic to be at head, got:\n{r.stdout}"
