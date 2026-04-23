"""TOTAL RESET — come back to zero with only the base tables.

Wipes every application table, then recreates the full baseline schema
(the 7 v2 tables + their indices). Used when you want a clean slate :
no data, no drift, just the base state defined by the migrations on disk.

Refuses to run if DATABASE_URL does not point to localhost.

Under the hood :
    alembic downgrade base   # drop everything
    alembic upgrade head     # rebuild base

This does NOT wipe the docker volume `postgres_data` (Postgres itself
and the `fxvol` database remain). For a true from-scratch volume wipe :

    docker compose -f docker-compose.dev.yml down -v
    docker compose -f docker-compose.dev.yml up -d postgres
    python scripts/db_apply.py

You are in charge of starting/stopping the postgres container yourself.
Shell commands (PowerShell) :

    # start
    docker compose -f docker-compose.dev.yml up -d postgres

    # env
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"

    # run the script
    python scripts/db_reset.py

    # stop
    docker compose -f docker-compose.dev.yml down
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "persistence" / "alembic.ini"


def _assert_localhost(url: str) -> None:
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(
            f"ERROR: reset refused, DATABASE_URL is not localhost (got {url!r})."
        )


def _alembic(*args: str) -> int:
    cmd = [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args]
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 2
    _assert_localhost(url)

    rc = _alembic("downgrade", "base")
    if rc != 0:
        print("downgrade base failed, aborting.", file=sys.stderr)
        return rc
    return _alembic("upgrade", "head")


if __name__ == "__main__":
    sys.exit(main())
