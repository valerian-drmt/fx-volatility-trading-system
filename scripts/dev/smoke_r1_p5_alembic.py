"""Functional smoke test of the R1 PR #5 Alembic initial migration.

Validates the migration round-trip against a live Postgres:
    1. downgrade base  : schema is empty (only alembic_version remains)
    2. upgrade head    : 7 expected tables exist, alembic_version = 001_initial_schema
    3. downgrade base  : rollback is complete, no orphan table
    4. upgrade head    : re-applying the migration works (idempotent round-trip)
    5. verify the ORM + migration stay in sync: Base.metadata.sorted_tables
       matches the list of public tables in Postgres after step 4

Usage (PowerShell):
    docker compose -f docker-compose.dev.yml up -d postgres
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
    python scripts/dev/smoke_r1_p5_alembic.py

Expected exit code : 0 on success, non-zero if any assertion fails.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import text  # noqa: E402

from persistence.db import get_engine, reset_engine_for_tests  # noqa: E402
from persistence.models import Base  # noqa: E402

EXPECTED_TABLES = sorted(t.name for t in Base.metadata.sorted_tables)


def _banner(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _alembic(*args: str) -> None:
    cmd = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "persistence/alembic.ini",
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"alembic {' '.join(args)} failed")
    print(result.stderr.strip() or result.stdout.strip())


async def _list_public_tables() -> list[str]:
    reset_engine_for_tests()
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "ORDER BY tablename"
            )
        )
        return [row[0] for row in result.all()]


async def _current_alembic_version() -> str | None:
    reset_engine_for_tests()
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT version_num FROM alembic_version")
        )
        row = result.first()
        return row[0] if row else None


async def main() -> None:
    _banner("STEP 1 : downgrade base (reset schema)")
    _alembic("downgrade", "base")
    tables = await _list_public_tables()
    # only alembic_version may remain
    business = [t for t in tables if t != "alembic_version"]
    assert business == [], f"expected no business tables, got {business}"
    print(f"  OK : business tables after downgrade = {business}")

    _banner("STEP 2 : upgrade head (apply initial_schema)")
    _alembic("upgrade", "head")
    tables = await _list_public_tables()
    business = sorted(t for t in tables if t != "alembic_version")
    assert business == EXPECTED_TABLES, (
        f"expected {EXPECTED_TABLES}, got {business}"
    )
    print(f"  OK : {len(business)} tables created : {', '.join(business)}")
    version = await _current_alembic_version()
    assert version == "001_initial_schema", f"expected 001_initial_schema, got {version}"
    print(f"  OK : alembic_version = {version!r}")

    _banner("STEP 3 : downgrade base again (rollback)")
    _alembic("downgrade", "base")
    tables = await _list_public_tables()
    business = [t for t in tables if t != "alembic_version"]
    assert business == [], f"expected rollback complete, got {business}"
    print("  OK : rollback complete")

    _banner("STEP 4 : upgrade head (round-trip)")
    _alembic("upgrade", "head")
    tables = await _list_public_tables()
    business = sorted(t for t in tables if t != "alembic_version")
    assert business == EXPECTED_TABLES
    print(f"  OK : re-applied. {len(business)} tables present.")

    _banner("STEP 5 : ORM models and DB schema are in sync")
    orm_tables = sorted(EXPECTED_TABLES)
    db_tables = business
    assert orm_tables == db_tables, f"drift: ORM={orm_tables} vs DB={db_tables}"
    print(f"  OK : Base.metadata tables == public tables ({len(orm_tables)} each)")

    print()
    print("=" * 70)
    print("  ALL 5 FUNCTIONAL CHECKS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
