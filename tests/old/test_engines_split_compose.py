"""End-to-end integration : docker compose stack with the 4 R7 engine
containers + postgres + redis, validates the architectural invariants
the split is supposed to deliver (isolation, granular restart,
persistence via db-writer).

Gated behind ``ENGINES_RUN_INTEGRATION=1`` because it spins up the full
Docker stack and takes ~2 minutes. Without IB credentials the three
IB-bound engines loop on reconnect — the tests that require real
market data are documented as manual in the spec and skipped here
(they would need ``IB_RUN_INTEGRATION=1`` + ``IB_USERID`` secrets).

What this suite actually exercises without IB :
- the 4 containers **start** and reach state ``running`` on compose up
- **db-writer** subscribes Redis and commits events to Postgres
  (market-data + vol + risk are cycling but their db_events publishes
  are not live without IB — we inject synthetic events from the test)
- **heartbeats** for db-writer refresh every 5s
- **granular restart** of db-writer leaves the other services untouched
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    os.environ.get("ENGINES_RUN_INTEGRATION") != "1",
    reason="Set ENGINES_RUN_INTEGRATION=1 to run the engines-split compose suite.",
)

SERVICES = ("market-data", "vol-engine", "risk-engine", "db-writer")
COMPOSE_ENV = {
    **os.environ,
    "DB_PASSWORD": os.environ.get("DB_PASSWORD", "fxvol"),
    "VNC_PASSWORD": os.environ.get("VNC_PASSWORD", "vncpass"),
}


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=REPO_ROOT, env=COMPOSE_ENV, capture_output=True, text=True,
        check=False, timeout=timeout,
    )


def _container_state(name: str) -> str:
    r = _run(["docker", "inspect", "-f", "{{.State.Status}}", name])
    return r.stdout.strip() or "missing"


def _wait_until_running(containers: tuple[str, ...], timeout: int = 120) -> dict[str, str]:
    deadline = time.time() + timeout
    states: dict[str, str] = {}
    while time.time() < deadline:
        states = {name: _container_state(name) for name in containers}
        if all(s == "running" for s in states.values()):
            return states
        time.sleep(3)
    return states


@pytest.fixture(scope="module", autouse=True)
def _compose_stack():
    """Boot the engines profile for the module, tear it down at exit."""
    up = _run(
        ["docker", "compose", "--profile", "engines", "up", "-d", "--build"],
        timeout=600,
    )
    assert up.returncode == 0, f"compose up failed: {up.stderr[-1000:]}"

    # Apply alembic migrations (db-writer will INSERT into these tables).
    _run(
        ["docker", "compose", "exec", "-T", "api",
         "python", "-m", "alembic", "-c", "src/persistence/alembic.ini", "upgrade", "head"],
    )

    yield

    _run(["docker", "compose", "--profile", "engines", "down", "--remove-orphans"], timeout=120)


def test_all_four_engine_containers_start():
    containers = tuple(f"fxvol-{name}" for name in SERVICES)
    states = _wait_until_running(containers, timeout=120)
    stopped = {k: v for k, v in states.items() if v != "running"}
    assert not stopped, (
        f"engines not running: {stopped}. "
        f"Without IB credentials, the three IB-bound engines loop on reconnect "
        f"but their container state stays `running`."
    )


def test_db_writer_persists_injected_events():
    """Publish a synthetic db_events frame on Redis, check Postgres row landed."""
    row_marker = f"integration-{int(time.time())}"
    event = {
        "table": "account_snaps",
        "payload": {
            "timestamp": "2026-04-21T10:00:00+00:00",
            "net_liq_usd": 999_999.99,
            "cash_usd": 0.0,
            "currencies": {"marker": row_marker},
        },
    }

    # Publish via redis-cli inside the redis container — no host-side redis dep.
    pub = _run([
        "docker", "compose", "exec", "-T", "redis",
        "redis-cli", "PUBLISH", "db_events", json.dumps(event),
    ])
    assert pub.returncode == 0, pub.stderr

    # Give db-writer ~8s to pick it up + flush (batch timer 5s).
    time.sleep(8)

    query = _run([
        "docker", "compose", "exec", "-T", "postgres",
        "psql", "-U", "fxvol", "-d", "fxvol", "-t", "-A",
        "-c", f"SELECT COUNT(*) FROM account_snaps WHERE currencies->>'marker' = '{row_marker}';",
    ])
    assert query.returncode == 0, query.stderr
    count = int(query.stdout.strip())
    assert count >= 1, f"expected >=1 row with marker={row_marker}, got {count}"


def test_db_writer_heartbeat_refreshes():
    """The heartbeat loop (5s cadence) must bump the Redis key continuously."""
    first = _run([
        "docker", "compose", "exec", "-T", "redis",
        "redis-cli", "GET", "heartbeat:db_writer",
    ])
    assert first.returncode == 0 and first.stdout.strip(), "no heartbeat found"

    time.sleep(7)
    second = _run([
        "docker", "compose", "exec", "-T", "redis",
        "redis-cli", "GET", "heartbeat:db_writer",
    ])
    assert second.returncode == 0
    assert float(second.stdout.strip()) > float(first.stdout.strip()), (
        "heartbeat timestamp did not advance in 7s — heartbeat loop stuck"
    )


def test_graceful_restart_of_db_writer_leaves_others_running():
    """Restart db-writer only ; the three engine containers stay up."""
    before = {name: _container_state(f"fxvol-{name}") for name in SERVICES}

    restart = _run(["docker", "compose", "restart", "db-writer"], timeout=60)
    assert restart.returncode == 0, restart.stderr

    # Give db-writer ~20s to come back up.
    _wait_until_running(("fxvol-db-writer",), timeout=30)

    after = {name: _container_state(f"fxvol-{name}") for name in SERVICES}
    others_before = {k: v for k, v in before.items() if k != "db-writer"}
    others_after = {k: v for k, v in after.items() if k != "db-writer"}
    assert others_before == others_after, (
        f"granular restart leaked : others before={others_before} after={others_after}"
    )


def test_market_data_crash_does_not_kill_vol_or_risk():
    """kill market-data → vol-engine and risk-engine containers stay up."""
    assert _container_state("fxvol-vol-engine") == "running"
    assert _container_state("fxvol-risk-engine") == "running"

    kill = _run(["docker", "compose", "kill", "market-data"], timeout=30)
    assert kill.returncode == 0, kill.stderr

    time.sleep(3)
    vol_after = _container_state("fxvol-vol-engine")
    risk_after = _container_state("fxvol-risk-engine")

    assert vol_after == "running", f"vol died with market-data : {vol_after}"
    assert risk_after == "running", f"risk died with market-data : {risk_after}"

    # Bring market-data back for subsequent tests.
    _run(["docker", "compose", "start", "market-data"], timeout=60)


@pytest.mark.skip(
    reason="requires live IB paper credentials — run manually with IB_USERID / IB_PASSWORD"
)
def test_engines_produce_data_end_to_end():
    """Spot + vol_surface + greeks present in Redis, <30s old. IB-bound."""
