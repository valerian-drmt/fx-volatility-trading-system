"""RECORD a schema change — turn a model edit into a new migration file.

After you add/remove/modify a table or column in src/persistence/models.py,
this script writes a new migration file that captures the diff. The file
is NOT applied automatically — you review it first, then run db_apply.py.

The DB must already be at head (run db_apply.py first). Autogenerate
misses some things : DESC indices, partial indices, GIN, column renames.
Always review the generated file before applying.

Under the hood :
    alembic revision --autogenerate -m "<your message>"

Typical workflow to ADD or REMOVE a table (or a column) :
    1. Edit src/persistence/models.py
    2. python scripts/db_apply.py                (DB at head)
    3. python scripts/db_new_revision.py "add notes column to positions"
    4. Review persistence/migrations/versions/<new file>.py
    5. python scripts/db_apply.py                (run it)
    6. python scripts/db_rollback.py             (verify round-trip)
    7. python scripts/db_apply.py                (back to head)

You are in charge of starting/stopping the postgres container yourself.
Shell commands (PowerShell) :

    # start
    docker compose -f docker-compose.dev.yml up -d postgres

    # env
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"

    # run the script (message mandatory)
    python scripts/db_new_revision.py "short summary in imperative"

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


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 2
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(
            'ERROR: missing revision message.\n'
            '  python scripts/db_new_revision.py "add notes column to positions"',
            file=sys.stderr,
        )
        return 2

    message = sys.argv[1]
    cmd = [
        sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI),
        "revision", "--autogenerate", "-m", message,
    ]
    print(f"$ {' '.join(cmd)}")
    rc = subprocess.run(cmd, cwd=PROJECT_ROOT).returncode
    if rc == 0:
        print(
            "\nNext steps:\n"
            "  1. Review the file in persistence/migrations/versions/\n"
            "  2. python scripts/db_apply.py\n"
            "  3. python scripts/db_rollback.py  (verify round-trip)\n"
            "  4. python scripts/db_apply.py     (back to head)"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
