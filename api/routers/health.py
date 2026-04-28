"""Health and liveness endpoints — /api/v1/health (basic) + /extended."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from redis import asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis
from bus import keys

router = APIRouter(prefix="/api/v1", tags=["health"])

# A heartbeat older than HEARTBEAT_STALE_S is flagged STALE ; missing = DOWN.
HEARTBEAT_STALE_S: int = 30
ENGINES: tuple[str, ...] = (keys.ENGINE_MARKET_DATA, keys.ENGINE_VOL, keys.ENGINE_RISK)


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — always 200 as long as the FastAPI process answers."""
    return {"status": "OK"}


@router.get("/health/extended")
async def health_extended(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Readiness probe — Redis + DB + engine heartbeats. Aggregate status.

    Status is ``DEGRADED`` if any subsystem is not OK, else ``OK``.
    Redis heartbeats are the authoritative engine liveness signal
    (written by the engines every ~1s via ``set_heartbeat``).
    """
    redis_status = await _check_redis(redis)
    db_status = await _check_db(db)
    engines_status = await _check_engines(redis)

    all_ok = (
        redis_status == "OK"
        and db_status == "OK"
        and all(v == "OK" for v in engines_status.values())
    )
    return {
        "status": "OK" if all_ok else "DEGRADED",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "components": {
            "redis": redis_status,
            "database": db_status,
            "engines": engines_status,
        },
    }


async def _check_redis(redis: aioredis.Redis) -> str:
    try:
        return "OK" if await redis.ping() else "DOWN"
    except Exception:
        return "DOWN"


async def _check_db(db: AsyncSession) -> str:
    try:
        await db.execute(text("SELECT 1"))
        return "OK"
    except Exception:
        return "DOWN"


async def _check_engines(redis: aioredis.Redis) -> dict[str, str]:
    """Per-engine status : OK (<30s), STALE (>=30s), DOWN (no key)."""
    statuses: dict[str, str] = {}
    now = datetime.now(UTC)
    for engine in ENGINES:
        try:
            raw = await redis.get(keys.HEARTBEAT.format(engine_name=engine))
        except Exception:
            statuses[engine] = "DOWN"
            continue
        if not raw:
            statuses[engine] = "DOWN"
            continue
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            age = (now - ts).total_seconds()
            statuses[engine] = "OK" if age < HEARTBEAT_STALE_S else f"STALE ({age:.0f}s)"
        except (ValueError, TypeError):
            statuses[engine] = "DOWN"
    return statuses
