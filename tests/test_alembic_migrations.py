"""Integration tests for the Alembic migration chain (R1 PR #7).

Executed only when DB_RUN_INTEGRATION=1 is set (see conftest.py). Each test
drives Alembic against a live PostgreSQL instance reachable via DATABASE_URL
and inspects information_schema / pg_indexes to prove the expected state.

Locally:
    docker compose -f docker-compose.dev.yml up -d postgres
    set DATABASE_URL=postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol
    set DB_RUN_INTEGRATION=1
    python -m pytest tests/test_alembic_migrations.py -v

On CI the job 'db-migrations' provides the postgres service and sets both
environment variables before invoking pytest.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db_integration

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "persistence" / "alembic.ini"

EXPECTED_TABLES = {
    "positions",
    "position_snapshots",
    "vol_surfaces",
    "signals",
    "trades",
    "account_snaps",
    "backtest_runs",
}

EXPECTED_INDICES = {
    "idx_positions_symbol_status",
    "idx_positions_entry_ts",
    "idx_positions_status_active",
    "idx_pos_snaps_position_ts",
    "idx_pos_snaps_ts",
    "idx_vol_surf_underlying_ts",
    "idx_vol_surf_ts",
    "idx_vol_surf_data_gin",
    "idx_signals_underlying_tenor_ts",
    "idx_signals_type_ts",
    "idx_signals_ts",
    "idx_trades_position",
    "idx_trades_ts",
    "idx_account_ts",
    "idx_backtest_strategy",
    "idx_backtest_created",
}


def _alembic(command: str) -> None:
    """Run `alembic -c persistence/alembic.ini <command>` from the repo root.

    Shelling out instead of importing Alembic's Python API keeps the test
    identical to what a developer (or the CI step) would type by hand and
    exercises alembic.ini resolution, env.py bootstrap, and DATABASE_URL
    reading end-to-end.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *command.split()],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic {command} failed (code {result.returncode})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _sync_url() -> str:
    """Return a psycopg2 URL derived from DATABASE_URL (async driver -> sync)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@pytest.fixture
def clean_db():
    """Ensure the DB is empty before the test and reset after it.

    Drops alembic_version + any application tables/indices left over from a
    previous run. Tests assume they start on a clean schema.
    """
    engine = create_engine(_sync_url(), future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def test_migration_upgrade_from_scratch(clean_db):
    """alembic upgrade head on an empty DB creates the 7 v2 tables."""
    _alembic("upgrade head")

    engine = create_engine(_sync_url(), future=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        ).all()
    engine.dispose()

    tables = {r[0] for r in rows}
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables after upgrade: {missing}"
    assert "alembic_version" in tables, "alembic_version bookkeeping table absent"


def test_migration_downgrade_then_upgrade(clean_db):
    """Full round-trip: upgrade head, downgrade base, upgrade head again."""
    _alembic("upgrade head")
    _alembic("downgrade base")

    engine = create_engine(_sync_url(), future=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        ).all()
    tables_after_downgrade = {r[0] for r in rows}
    assert EXPECTED_TABLES.isdisjoint(tables_after_downgrade), (
        f"downgrade base left application tables behind: "
        f"{EXPECTED_TABLES & tables_after_downgrade}"
    )

    _alembic("upgrade head")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        ).all()
    engine.dispose()
    tables_final = {r[0] for r in rows}
    assert tables_final >= EXPECTED_TABLES, (
        f"re-upgrade did not recreate all tables: {EXPECTED_TABLES - tables_final}"
    )


def test_all_indices_created(clean_db):
    """After upgrade head, every index declared in 002_add_indices is present."""
    _alembic("upgrade head")

    engine = create_engine(_sync_url(), future=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        ).all()
    engine.dispose()

    indices = {r[0] for r in rows}
    missing = EXPECTED_INDICES - indices
    assert not missing, f"missing indices after upgrade: {missing}"
