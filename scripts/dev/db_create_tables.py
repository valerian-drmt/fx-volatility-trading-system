"""Create all ORM tables in Postgres from persistence.models.Base.metadata.

Temporary bootstrap used before Alembic migrations are introduced in R1 PR #5.
Idempotent : re-running is safe (SQLAlchemy emits CREATE IF NOT EXISTS).

Usage (PowerShell):
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5432/fxvol"
    python scripts/dev/db_create_tables.py

The script adds `src/` to sys.path itself so it runs from any cwd and
from any IDE run configuration without needing PYTHONPATH=src.

After R1 PR #5, prefer:
    alembic upgrade head
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from persistence.db import get_engine  # noqa: E402
from persistence.models import Base  # noqa: E402


async def main() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    tables = sorted(t.name for t in Base.metadata.sorted_tables)
    print(f"Created / ensured {len(tables)} tables : {', '.join(tables)}")


if __name__ == "__main__":
    asyncio.run(main())
