"""Functional smoke test of the R1 PR #6 indices migration.

Validates on a live Postgres that migration 002_add_indices creates
every expected index with the correct type, columns and filter:

    1. upgrade head : 16 indices with names idx_* present in pg_indexes
    2. GIN on vol_surfaces.surface_data : indexdef contains 'USING gin'
    3. Partial on positions.status_active : indexdef contains 'WHERE'
    4. DESC sort on *_ts indices : indexdef contains '"timestamp" DESC'
    5. downgrade -1 : all idx_* removed, 0 remain
    6. upgrade head again : 16 indices restored

Usage (PowerShell):
    docker compose -f docker-compose.dev.yml up -d postgres
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
    python scripts/dev/smoke_r1_p6_indices.py
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


def _banner(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _alembic(*args: str) -> None:
    cmd = [sys.executable, "-m", "alembic", "-c", "persistence/alembic.ini", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"alembic {' '.join(args)} failed")
    print(result.stderr.strip() or result.stdout.strip())


async def _list_indices() -> dict[str, str]:
    reset_engine_for_tests()
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND indexname LIKE 'idx_%'"
            )
        )
        return {row[0]: row[1] for row in result.all()}


async def main() -> None:
    _banner("STEP 1 : reset + upgrade head")
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")

    indices = await _list_indices()
    missing = EXPECTED_INDICES - indices.keys()
    extra = indices.keys() - EXPECTED_INDICES
    assert not missing, f"missing indices: {missing}"
    assert not extra, f"unexpected indices: {extra}"
    print(f"  OK : {len(indices)} indices present, matches spec exactly")

    _banner("STEP 2 : GIN on vol_surfaces.surface_data")
    gin_def = indices["idx_vol_surf_data_gin"]
    assert "USING gin" in gin_def, f"expected GIN, got {gin_def!r}"
    assert "surface_data" in gin_def
    print(f"  OK : {gin_def}")

    _banner("STEP 3 : partial index on positions.status_active")
    partial_def = indices["idx_positions_status_active"]
    assert "WHERE" in partial_def, f"expected partial WHERE, got {partial_def!r}"
    assert "'OPEN'" in partial_def
    print(f"  OK : {partial_def}")

    _banner("STEP 4 : DESC sort on timestamp indices")
    for name in ["idx_pos_snaps_ts", "idx_vol_surf_ts", "idx_signals_ts", "idx_trades_ts"]:
        defn = indices[name]
        assert "DESC" in defn, f"{name} missing DESC in {defn!r}"
    print("  OK : 4 timestamp indices use DESC sort")

    _banner("STEP 5 : downgrade -1 removes all idx_*")
    _alembic("downgrade", "-1")
    indices = await _list_indices()
    assert indices == {}, f"expected 0 indices after downgrade, got {list(indices)}"
    print("  OK : 0 indices after downgrade")

    _banner("STEP 6 : upgrade head restores indices")
    _alembic("upgrade", "head")
    indices = await _list_indices()
    assert set(indices) == EXPECTED_INDICES
    print(f"  OK : {len(indices)} indices restored")

    print()
    print("=" * 70)
    print("  ALL 6 FUNCTIONAL CHECKS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
