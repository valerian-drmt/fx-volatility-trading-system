"""ADD tables / columns — bring the DB up to the latest schema.

Runs every migration not yet applied, in order. If a revision adds a new
table or column, it is created. If the DB is already up-to-date, nothing
happens (idempotent).

Under the hood :
    alembic upgrade head           # apply all pending revisions
    alembic upgrade <target>       # stop at a specific revision (optional)

You are in charge of starting/stopping the postgres container yourself.
Shell commands (PowerShell) :

    # start
    docker compose -f docker-compose.dev.yml up -d postgres

    # env
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"

    # run the script
    python scripts/db_apply.py                   # default target = head
    python scripts/db_apply.py 001_initial_schema

    # stop (volume preserved — data survives between runs)
    docker compose -f docker-compose.dev.yml down
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "persistence" / "alembic.ini"


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 2

    target = sys.argv[1] if len(sys.argv) > 1 else "head"
    cmd = [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", target]
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
