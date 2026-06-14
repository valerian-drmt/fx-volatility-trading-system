"""Analytics endpoint helpers — vol history + system stats.

Per-tenor pricing signals (CHEAP / FAIR / EXPENSIVE) and the associated
``vol_pricing_signal_snapshot`` table were dropped in R9 once the trading
strategy switched to PCA-only — the matching ``list_signals`` helper +
``/api/v1/analytics/signals`` route went with them.
"""
from __future__ import annotations

from datetime import UTC, datetime

from redis import asyncio as aioredis
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.analytics import (
    EngineStats,
    SystemStats,
    VolHistoryRow,
)
from bus import keys
from persistence.models import (
    PositionSnapshot,
    VolSurface,
)

# Tables reported in /system-stats — tuple keeps order stable.
_COUNTED_TABLES: tuple[tuple[str, type], ...] = (
    ("vol_surface_history", VolSurface),
    ("position_snapshots", PositionSnapshot),
)

_MONITORED_ENGINES: tuple[str, ...] = (
    keys.ENGINE_MARKET_DATA, keys.ENGINE_VOL, keys.ENGINE_RISK,
)


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
