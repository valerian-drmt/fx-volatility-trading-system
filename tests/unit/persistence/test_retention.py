"""History retention (#330, audit DI-03 / DI-35).

A position's history survives the close (no FK, no cascade); each table is
pruned in its own transaction so one failure never stops the others; a PCA
signal a trade still references is excluded from the delete instead of
blocking it; failures are counted.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=90)


async def _seed_position_with_history(maker: Any) -> int:
    from persistence.models import OpenPosition, OpenPositionHistory
    async with maker() as db:
        p = OpenPosition(structure="EUUZ6 C1130", side="BUY", quantity=Decimal("10"), entry_timestamp=NOW)
        db.add(p)
        await db.flush()
        for days_ago in (120, 100, 10, 1):
            db.add(OpenPositionHistory(position_id=p.id, timestamp=NOW - timedelta(days=days_ago),
                                       structure=p.structure, side="BUY", quantity=Decimal("10")))
        await db.commit()
        return int(p.id)


async def _count(maker: Any, model: Any) -> int:
    from sqlalchemy import func, select
    async with maker() as db:
        return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def test_closing_a_position_keeps_its_history(sqlite_sessionmaker: Any) -> None:
    """DI-03: the mirror row goes, the leg's greeks / P&L series stays."""
    from persistence.models import OpenPosition, OpenPositionHistory

    pid = await _seed_position_with_history(sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        row = await db.get(OpenPosition, pid)
        await db.delete(row)
        await db.commit()
    assert await _count(sqlite_sessionmaker, OpenPosition) == 0
    assert await _count(sqlite_sessionmaker, OpenPositionHistory) == 4
    assert not hasattr(OpenPositionHistory, "position") and not hasattr(OpenPosition, "metric_history")


async def test_retention_prunes_each_table_on_its_own_and_reports_rows(sqlite_sessionmaker: Any) -> None:
    from persistence.models import AccountHistory, OpenPositionHistory
    from persistence.retention import RISK_ENGINE_TABLES, run_retention

    await _seed_position_with_history(sqlite_sessionmaker)
    async with sqlite_sessionmaker() as db:
        for days_ago in (200, 95, 5):
            db.add(AccountHistory(timestamp=NOW - timedelta(days=days_ago), net_liq_usd=Decimal("1")))
        await db.commit()

    out = await run_retention(sqlite_sessionmaker, engine="risk_engine", cutoff=CUTOFF, tables=RISK_ENGINE_TABLES)

    assert out == {"open_position_history": 2, "account_history": 2}
    assert await _count(sqlite_sessionmaker, OpenPositionHistory) == 2
    assert await _count(sqlite_sessionmaker, AccountHistory) == 1


async def test_a_referenced_pca_signal_is_kept_and_does_not_block_the_prune(sqlite_sessionmaker: Any) -> None:
    """DI-35: the old signal a trade points at survives; the other old ones go;
    the surface table in the same pass is pruned too."""
    from sqlalchemy import select

    from persistence.models import PcaModel, PcaSignal, TradeStructure, VolSurface
    from persistence.retention import VOL_ENGINE_TABLES, run_retention

    async with sqlite_sessionmaker() as db:
        model = PcaModel(version="v1", fit_window_start=NOW - timedelta(days=400), fit_window_end=NOW,
                         n_obs_used=10, means=[0], stds=[1], loadings=[[1]], eigenvalues=[1],
                         variance_explained_ratio=[1.0])
        db.add(model)
        await db.flush()
        sigs = []
        for days_ago in (200, 150, 3):
            s = PcaSignal(timestamp=NOW - timedelta(days=days_ago), symbol="EURUSD", pca_model_id=model.id, pc_id=1,
                          raw_score=Decimal("0.1"), z_score=Decimal("1.5"), label="CHEAP", actionable=True)
            db.add(s)
            sigs.append(s)
        await db.flush()
        db.add(TradeStructure(structure_type="straddle", reference_tenor="3M", base_qty=1, state="closed",
                              execution_mode="live", pca_signal_id=sigs[0].id))
        db.add(VolSurface(timestamp=NOW - timedelta(days=120), underlying="EURUSD", spot=Decimal("1.1"),
                          forward=Decimal("1.1"), surface_data={}))
        db.add(VolSurface(timestamp=NOW - timedelta(days=2), underlying="EURUSD", spot=Decimal("1.1"),
                          forward=Decimal("1.1"), surface_data={}))
        await db.commit()
        referenced_id = sigs[0].id

    out = await run_retention(sqlite_sessionmaker, engine="vol_engine", cutoff=CUTOFF, tables=VOL_ENGINE_TABLES)

    assert out == {"vol_surface_history": 1, "pca_signal_history": 1}
    async with sqlite_sessionmaker() as db:
        left = sorted((await db.execute(select(PcaSignal.id))).scalars().all())
    assert referenced_id in left and len(left) == 2


async def test_one_failing_table_does_not_stop_the_others_and_is_counted(sqlite_sessionmaker: Any) -> None:
    from persistence.models import AccountHistory
    from persistence.retention import PruneSpec, run_retention
    from shared.observability import history_prune_failures_total

    async with sqlite_sessionmaker() as db:
        db.add(AccountHistory(timestamp=NOW - timedelta(days=200), net_liq_usd=Decimal("1")))
        await db.commit()
    before = history_prune_failures_total.labels(engine="t", table="vol_surface_history")._value.get()
    specs = (
        PruneSpec("vol_surface_history", extra_where="AND this_column_does_not_exist = 1"),  # fails
        PruneSpec("account_history"),
    )
    out = await run_retention(sqlite_sessionmaker, engine="t", cutoff=CUTOFF, tables=specs)
    assert out == {"vol_surface_history": "failed", "account_history": 1}
    assert history_prune_failures_total.labels(engine="t", table="vol_surface_history")._value.get() == before + 1


async def test_whitelist_refuses_an_unknown_table(sqlite_sessionmaker: Any) -> None:
    from persistence.retention import PruneSpec, prune_table

    async with sqlite_sessionmaker() as db:
        with pytest.raises(ValueError):
            await prune_table(db, PruneSpec("trade_fill"), CUTOFF)
