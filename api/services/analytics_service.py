"""Analytics endpoint helpers — signals, vol history, backtests, system stats."""
from __future__ import annotations

from datetime import UTC, datetime

from redis import asyncio as aioredis
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.analytics import (
    BacktestRunRow,
    EngineStats,
    SignalRow,
    SystemStats,
    VolHistoryRow,
)
from bus import keys
from persistence.models import (
    BacktestRun,
    PositionSnapshot,
    Signal,
    VolSurface,
)

# Tables reported in /system-stats — tuple keeps order stable.
_COUNTED_TABLES: tuple[tuple[str, type], ...] = (
    ("signals", Signal),
    ("vol_surfaces", VolSurface),
    ("position_snapshots", PositionSnapshot),
    ("backtest_runs", BacktestRun),
)

_MONITORED_ENGINES: tuple[str, ...] = (
    keys.ENGINE_MARKET_DATA, keys.ENGINE_VOL, keys.ENGINE_RISK,
)


async def list_signals(
    db: AsyncSession,
    underlying: str | None = None,
    tenor: str | None = None,
    signal_type: str | None = None,
    since: datetime | None = None,
    limit: int = 200,
) -> list[SignalRow]:
    """Recent signals, most-recent first. All filters optional and combinable."""
    stmt = select(Signal).order_by(desc(Signal.timestamp)).limit(limit)
    if underlying:
        stmt = stmt.where(Signal.underlying == underlying)
    if tenor:
        stmt = stmt.where(Signal.tenor == tenor)
    if signal_type:
        stmt = stmt.where(Signal.signal_type == signal_type.upper())
    if since:
        stmt = stmt.where(Signal.timestamp >= since)
    rows = (await db.execute(stmt)).scalars().all()
    return [SignalRow.model_validate(r) for r in rows]


async def vol_history(
    db: AsyncSession, symbol: str = "EURUSD", limit: int = 50
) -> list[VolHistoryRow]:
    """N latest vol_surfaces entries for ``symbol``, headline fields only."""
    stmt = (
        select(VolSurface)
        .where(VolSurface.underlying == symbol)
        .order_by(desc(VolSurface.timestamp))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [VolHistoryRow.model_validate(r) for r in rows]


async def list_backtests(
    db: AsyncSession, strategy_name: str | None = None, limit: int = 50
) -> list[BacktestRunRow]:
    stmt = select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(limit)
    if strategy_name:
        stmt = stmt.where(BacktestRun.strategy_name == strategy_name)
    rows = (await db.execute(stmt)).scalars().all()
    return [BacktestRunRow.model_validate(r) for r in rows]


async def system_stats(
    db: AsyncSession, redis: aioredis.Redis
) -> SystemStats:
    """Aggregate : row counts (PG) + engine heartbeat ages (Redis)."""
    counts: dict[str, int] = {}
    for name, model in _COUNTED_TABLES:
        counts[name] = (
            await db.execute(select(func.count()).select_from(model))
        ).scalar_one()

    engines = [await _engine_stats(redis, name) for name in _MONITORED_ENGINES]
    return SystemStats(
        timestamp=datetime.now(UTC), counts=counts, engines=engines
    )


async def _engine_stats(redis: aioredis.Redis, name: str) -> EngineStats:
    try:
        raw = await redis.get(keys.HEARTBEAT.format(engine_name=name))
    except Exception:
        return EngineStats(name=name, state="DOWN", heartbeat_age_s=None)
    if not raw:
        return EngineStats(name=name, state="DOWN", heartbeat_age_s=None)
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        age = (datetime.now(UTC) - ts).total_seconds()
        state = "OK" if age < 30 else f"STALE ({age:.0f}s)"
        return EngineStats(name=name, state=state, heartbeat_age_s=age)
    except (ValueError, TypeError):
        return EngineStats(name=name, state="DOWN", heartbeat_age_s=None)
