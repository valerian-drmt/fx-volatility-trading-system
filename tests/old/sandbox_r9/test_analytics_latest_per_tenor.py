"""Tests for api.orchestration.analytics_service.list_signals(latest_per_tenor=True).

Each vol-engine cycle emits one Signal row per tenor, so over time the
table stacks dozens of duplicates. The scanner dashboard wants only
the most recent row per (underlying, tenor) pair.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("pytest_asyncio")


async def _seed_and_session(rows):
    """Spin up an in-memory sqlite db with the full schema + seed rows.

    Returns (maker, engine) — caller must dispose the engine at the end.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base, Signal

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        for r in rows:
            session.add(Signal(**r))
        await session.commit()
    return maker, engine


@pytest.mark.asyncio
async def test_latest_per_tenor_collapses_duplicates() -> None:
    from api.orchestration.analytics_service import list_signals

    base = datetime(2026, 4, 22, 14, 0, tzinfo=UTC)
    rows = []
    for i, cycle_off in enumerate([0, 1, 2]):
        ts = base + timedelta(minutes=cycle_off * 30)
        for tenor, dte in [("1M", 30), ("2M", 60)]:
            rows.append({
                "timestamp": ts, "underlying": "EURUSD", "tenor": tenor, "dte": dte,
                "sigma_mid": 6.0 + i * 0.1, "sigma_fair": 2.5,
                "ecart": 3.5 + i * 0.1, "signal_type": "EXPENSIVE",
                "rv": 2.87,
            })
    maker, engine = await _seed_and_session(rows)
    try:
        async with maker() as session:
            flat = await list_signals(session)
            assert len(flat) == 6

            collapsed = await list_signals(session, latest_per_tenor=True)
            assert len(collapsed) == 2
            assert {r.tenor for r in collapsed} == {"1M", "2M"}
            # sqlite stores naive datetimes — compare without tzinfo.
            expected = (base + timedelta(minutes=60)).replace(tzinfo=None)
            for r in collapsed:
                assert r.timestamp.replace(tzinfo=None) == expected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_latest_per_tenor_respects_filters() -> None:
    from api.orchestration.analytics_service import list_signals

    base = datetime(2026, 4, 22, 14, 0, tzinfo=UTC)
    rows = [
        {"timestamp": base, "underlying": "EURUSD", "tenor": "1M", "dte": 30,
         "sigma_mid": 6.0, "sigma_fair": 2.5, "ecart": 3.5, "signal_type": "EXPENSIVE"},
        {"timestamp": base + timedelta(minutes=30), "underlying": "EURUSD", "tenor": "1M", "dte": 30,
         "sigma_mid": 6.1, "sigma_fair": 2.5, "ecart": 3.6, "signal_type": "EXPENSIVE"},
        {"timestamp": base, "underlying": "GBPUSD", "tenor": "1M", "dte": 30,
         "sigma_mid": 8.0, "sigma_fair": 7.5, "ecart": 0.5, "signal_type": "FAIR"},
    ]
    maker, engine = await _seed_and_session(rows)
    try:
        async with maker() as session:
            out = await list_signals(session, underlying="EURUSD", latest_per_tenor=True)
            assert len(out) == 1
            assert out[0].underlying == "EURUSD"
            assert float(out[0].ecart) == pytest.approx(3.6)
    finally:
        await engine.dispose()
