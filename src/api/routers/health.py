"""Health and liveness endpoints — /api/v1/health (basic) + /extended."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from prometheus_client import REGISTRY
from prometheus_client.core import GaugeMetricFamily
from redis import asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.dependencies import get_db_session, get_redis
from bus import keys

router = APIRouter(prefix="/api/v1", tags=["health"])

# A heartbeat older than HEARTBEAT_STALE_S is flagged STALE ; missing = DOWN.
HEARTBEAT_STALE_S: int = 30
# Nightly dump + slack: a stamp older than this means a backup run was missed
# (#343). The pre-deploy dump refreshes the stamp too, so a deploy never
# looks like a missed backup.
BACKUP_STALE_S: int = 26 * 3600
_STAMP_TS = re.compile(r"\bts=(\d+)\b")
# A downed Redis can make a ping/get BLOCK on connect (no implicit timeout), so
# every Redis probe is bounded — a health endpoint must answer fast even when a
# dependency is down (else the whole readiness read hangs and reads as all-DOWN).
REDIS_PROBE_TIMEOUT_S: float = 2.0
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
    now = datetime.now(UTC)
    redis_status = await _check_redis(redis)
    db_status = await _check_db(db)
    # Engine liveness is read from Redis heartbeats — short-circuit to DOWN when
    # Redis is unreachable instead of timing out once per engine.
    engines_status = (
        await _check_engines(redis) if redis_status == "OK"
        else {e: "DOWN" for e in ENGINES}
    )
    backup_status = _check_backup(_backup_state_path(), now)

    all_ok = (
        redis_status == "OK"
        and db_status == "OK"
        and all(v == "OK" for v in engines_status.values())
        and (backup_status is None or backup_status.startswith("OK"))
    )
    components: dict[str, Any] = {
        "redis": redis_status,
        "database": db_status,
        "engines": engines_status,
    }
    if backup_status is not None:
        components["backup"] = backup_status
    return {
        "status": "OK" if all_ok else "DEGRADED",
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "components": components,
    }


# --- backup freshness (#343) -------------------------------------------------
# infrastructure/ec2/postgres_backup.sh stamps one line after every successful
# S3 upload: ``ts=<epoch> size=<bytes> key=<s3 key>``. The api sees it through
# a read-only bind mount. Two consumers: the "backup" component above and the
# Prometheus gauge below (the alert rule belongs to the alerting ticket).


def _backup_state_path() -> str:
    return get_settings().BACKUP_STATE_FILE


def _backup_age_seconds(path: str, now: datetime) -> float | None:
    """Age of the stamp, or None when ``path`` is empty, missing or unreadable."""
    if not path:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    m = _STAMP_TS.search(raw)
    if not m:
        return None
    return max(0.0, now.timestamp() - int(m.group(1)))


def _check_backup(path: str, now: datetime) -> str | None:
    """None = not configured on this deployment (component omitted).
    ``UNKNOWN`` = configured but no readable stamp: the cron has never
    succeeded on this host, which counts as DEGRADED on purpose."""
    if not path:
        return None
    age = _backup_age_seconds(path, now)
    if age is None:
        return "UNKNOWN (no backup stamp)"
    hours = age / 3600
    return f"OK ({hours:.1f}h)" if age < BACKUP_STALE_S else f"STALE ({hours:.0f}h)"


class _BackupAgeCollector:
    """Prometheus collector: fxvol_postgres_backup_age_seconds, read at scrape
    time so the gauge is never a cached value. No sample when unconfigured."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        gauge = GaugeMetricFamily(
            "fxvol_postgres_backup_age_seconds",
            "Seconds since the last successful Postgres dump upload "
            "(from BACKUP_STATE_FILE); absent when no backup is configured.",
        )
        age = _backup_age_seconds(_backup_state_path(), datetime.now(UTC))
        if age is not None:
            gauge.add_metric([], age)
        yield gauge


try:
    REGISTRY.register(_BackupAgeCollector())
except ValueError:  # already registered (module re-import under tests)
    pass


async def _check_redis(redis: aioredis.Redis) -> str:
    try:
        return "OK" if await asyncio.wait_for(redis.ping(), REDIS_PROBE_TIMEOUT_S) else "DOWN"
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
            raw = await asyncio.wait_for(
                redis.get(keys.HEARTBEAT.format(engine_name=engine)), REDIS_PROBE_TIMEOUT_S
            )
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
