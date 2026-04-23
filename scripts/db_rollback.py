"""REMOVE the last changes — step the DB back one (or more) revisions.

Undoes the most recent migration by default : any table or column it had
added is dropped. Destructive on data (dropped tables are gone). Can also
step back to a specific revision, or down to empty (`base`).

Refuses to run if DATABASE_URL does not point to localhost.

Under the hood :
    alembic downgrade -1                       # undo the last migration
    alembic downgrade 001_initial_schema       # step back to a revision
    alembic downgrade base                     # drop every app table

You are in charge of starting/stopping the postgres container yourself.
Shell commands (PowerShell) :

    # start
    docker compose -f docker-compose.dev.yml up -d postgres

    # env
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"

    # run the script
    python scripts/db_rollback.py              # down -1 (one revision back)
    python scripts/db_rollback.py 001_initial_schema
    python scripts/db_rollback.py base         # drop every application table

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
            f"ERROR: rollback refused, DATABASE_URL is not localhost (got {url!r})."
        )


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 2
    _assert_localhost(url)

    target = sys.argv[1] if len(sys.argv) > 1 else "-1"
    cmd = [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "downgrade", target]
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
