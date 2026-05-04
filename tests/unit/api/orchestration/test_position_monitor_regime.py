"""Verifies that the position-monitor reads the latest RegimeSnapshot and
threads it into evaluate_all_rules so PreEventRegimeRule can fire."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


def _coerce_bigint_to_integer(metadata) -> None:
    from sqlalchemy import BigInteger, Integer
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()


async def _make_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base

    _coerce_bigint_to_integer(Base.metadata)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def test_load_latest_regime_returns_most_recent_label():
    from api.orchestration.position_monitor import PositionMonitorScheduler
    from persistence.models import RegimeSnapshot

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            db.add(RegimeSnapshot(
                timestamp=datetime(2026, 5, 4, 8, 0, tzinfo=UTC),
                symbol="EURUSD", label="calm", method="hmm_3state",
            ))
            db.add(RegimeSnapshot(
                timestamp=datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
                symbol="EURUSD", label="pre_event", method="hmm_3state",
            ))
            db.add(RegimeSnapshot(
                timestamp=datetime(2026, 5, 4, 9, 0, tzinfo=UTC),
                symbol="EURUSD", label="stressed", method="hmm_3state",
            ))
            await db.commit()

        sched = PositionMonitorScheduler(sessionmaker_factory=lambda: maker)
        async with maker() as db:
            label = await sched._load_latest_regime(db)
        assert label == "pre_event"
    finally:
        await engine.dispose()


async def test_load_latest_regime_returns_none_when_empty():
    from api.orchestration.position_monitor import PositionMonitorScheduler

    maker, engine = await _make_session()
    try:
        sched = PositionMonitorScheduler(sessionmaker_factory=lambda: maker)
        async with maker() as db:
            label = await sched._load_latest_regime(db)
        assert label is None
    finally:
        await engine.dispose()
