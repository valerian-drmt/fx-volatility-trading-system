"""Write-side hygiene (#373, hot/cold T9): the history write policy, the
batched nightly retention, the off-peak schedule, the Postgres sizing."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.risk.history_policy import HEARTBEAT_S, GreeksPoint, snapshot_due
from persistence.retention import (
    BATCH_ROWS,
    RETENTION_AT_UTC,
    RISK_ENGINE_TABLES,
    PruneSpec,
    next_retention_run,
    prune_table_batched,
    run_retention,
)

pytest.importorskip("pytest_asyncio")
ROOT = Path(__file__).resolve().parents[3]


def test_first_point_and_heartbeat_always_write() -> None:
    p = GreeksPoint(delta=1000.0, vega=50.0)
    assert snapshot_due(None, p, last_written_age_s=None)
    assert snapshot_due(p, p, last_written_age_s=HEARTBEAT_S)
    assert not snapshot_due(p, p, last_written_age_s=HEARTBEAT_S - 1)


def test_small_moves_are_quiet_and_real_moves_write() -> None:
    prev = GreeksPoint(delta=10_000.0, gamma=50.0, vega=800.0, theta=-30.0, pnl=120.0, iv=0.0812)
    quiet = GreeksPoint(delta=10_050.0, gamma=50.3, vega=804.0, theta=-30.2, pnl=120.5, iv=0.0814)
    assert not snapshot_due(prev, quiet, last_written_age_s=60.0)
    assert snapshot_due(prev, GreeksPoint(**{**prev.__dict__, "delta": 10_200.0}), last_written_age_s=60.0)
    assert snapshot_due(prev, GreeksPoint(**{**prev.__dict__, "pnl": 122.0}), last_written_age_s=60.0)
    assert snapshot_due(prev, GreeksPoint(**{**prev.__dict__, "iv": 0.0819}), last_written_age_s=60.0)
    # a tiny position: the $1 floor, not the 1 %, decides
    tiny = GreeksPoint(delta=20.0)
    assert not snapshot_due(tiny, GreeksPoint(delta=20.9), last_written_age_s=60.0)
    assert snapshot_due(tiny, GreeksPoint(delta=21.5), last_written_age_s=60.0)
    # a value that appears or disappears is a change
    assert snapshot_due(GreeksPoint(delta=None), GreeksPoint(delta=1.0), last_written_age_s=60.0)


def test_next_retention_run_is_the_next_0230_utc() -> None:
    assert RETENTION_AT_UTC == (2, 30)
    before = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
    after = datetime(2026, 11, 10, 2, 30, tzinfo=UTC)
    assert next_retention_run(before) == datetime(2026, 11, 10, 2, 30, tzinfo=UTC)
    assert next_retention_run(after) == datetime(2026, 11, 11, 2, 30, tzinfo=UTC)
    assert next_retention_run(datetime(2026, 11, 10, 23, 59, tzinfo=UTC)) == datetime(2026, 11, 11, 2, 30, tzinfo=UTC)


async def _seed(maker: Any, old_rows: int, new_rows: int, now: datetime) -> None:
    from persistence.models import OpenPosition, OpenPositionHistory

    async with maker() as db:
        p = OpenPosition(structure="EUUZ6 C1130", side="BUY", quantity=Decimal("10"), entry_timestamp=now)
        db.add(p)
        await db.flush()
        for i in range(old_rows):
            db.add(OpenPositionHistory(position_id=p.id, timestamp=now - timedelta(days=100, minutes=i),
                                       structure=p.structure, side="BUY", quantity=Decimal("10")))
        for i in range(new_rows):
            db.add(OpenPositionHistory(position_id=p.id, timestamp=now - timedelta(minutes=i),
                                       structure=p.structure, side="BUY", quantity=Decimal("10")))
        await db.commit()


async def _count(maker: Any) -> int:
    from sqlalchemy import func, select

    from persistence.models import OpenPositionHistory

    async with maker() as db:
        return int((await db.execute(select(func.count()).select_from(OpenPositionHistory))).scalar_one())


@pytest.mark.asyncio
async def test_batched_prune_removes_everything_past_the_cutoff_in_slices(sqlite_sessionmaker: Any) -> None:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    await _seed(sqlite_sessionmaker, old_rows=120, new_rows=5, now=now)
    n = await prune_table_batched(sqlite_sessionmaker, PruneSpec("open_position_history"),
                                  now - timedelta(days=90), batch_rows=50)
    assert n == 120 and await _count(sqlite_sessionmaker) == 5
    # idempotent: nothing left to prune
    assert await prune_table_batched(sqlite_sessionmaker, PruneSpec("open_position_history"),
                                     now - timedelta(days=90), batch_rows=50) == 0


@pytest.mark.asyncio
async def test_run_retention_uses_batches_and_reports_totals(sqlite_sessionmaker: Any) -> None:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    await _seed(sqlite_sessionmaker, old_rows=7, new_rows=3, now=now)
    summary = await run_retention(sqlite_sessionmaker, engine="risk_engine", cutoff=now - timedelta(days=90),
                                  tables=RISK_ENGINE_TABLES, batch_rows=3)
    assert summary["open_position_history"] == 7 and summary["account_history"] == 0
    assert await _count(sqlite_sessionmaker) == 3
    assert BATCH_ROWS == 50_000


def test_engines_schedule_the_prune_off_peak_and_the_risk_engine_writes_every_60s() -> None:
    risk = (ROOT / "src" / "engines" / "risk" / "engine.py").read_text(encoding="utf-8")
    vol = (ROOT / "src" / "engines" / "vol" / "engine.py").read_text(encoding="utf-8")
    for src in (risk, vol):
        assert "next_retention_run" in src and "PRUNE_EVERY_CYCLES" not in src
    assert "HISTORY_PERSIST_CYCLES = 30" in risk and "snapshot_due(" in risk


def test_postgres_is_sized_for_the_host_and_history_tables_get_autovacuum() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    cmd = compose["services"]["postgres"]["command"]
    joined = " ".join(cmd)
    for flag in ("shared_buffers=128MB", "effective_cache_size=768MB", "work_mem=8MB", "maintenance_work_mem=64MB"):
        assert flag in joined, flag
    import importlib.util

    path = ROOT / "src" / "persistence" / "migrations" / "versions" / "063_history_autovacuum.py"
    spec = importlib.util.spec_from_file_location("m063", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert set(mod.TABLES) == {"open_position_history", "booked_position_metric_history"}
    assert mod.down_revision == "062_config_append_only"
