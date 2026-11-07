"""Verifies that the position-monitor reads the latest RegimeSnapshot and
threads it into evaluate_all_rules so PreEventRegimeRule can fire."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.fixtures.db import make_sqlite_sessionmaker as _make_session  # shared harness (#338), FKs ON

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


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
