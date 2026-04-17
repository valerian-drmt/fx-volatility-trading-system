"""Drop all ORM tables in Postgres.

Inverse of db_create_tables.py. Used to reset the local dev database
to a clean state between manual-test sessions.

Usage (PowerShell):
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5432/fxvol"
    python scripts/dev/db_drop_tables.py

DANGER : drops every table declared in Base.metadata. Never run against
prod. The DATABASE_URL check below refuses to run if the host is not
localhost.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from persistence.db import get_engine  # noqa: E402
from persistence.models import Base  # noqa: E402


def _assert_localhost() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(
            f"Refusing to drop tables: DATABASE_URL does not point to localhost "
            f"(got {url!r})"
        )


async def main() -> None:
    _assert_localhost()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    tables = sorted(t.name for t in Base.metadata.sorted_tables)
    print(f"Dropped {len(tables)} tables : {', '.join(tables)}")


if __name__ == "__main__":
    asyncio.run(main())
