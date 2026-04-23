"""Admin config service — versioned read/write of :class:`VolTradingConfig`.

Every write produces a new row in ``vol_config`` (append-only) and
publishes ``config:changed`` on Redis so consuming services
(vol-engine, risk-engine, ...) hot-reload without a restart.

The service is deliberately thin : Pydantic does schema validation,
SQLAlchemy does transaction boundary, Redis does fan-out.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from redis import asyncio as aioredis
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import VolTradingConfig
from persistence.models import VolConfig

CONFIG_CHANGED_CHANNEL = "config:changed"


@dataclass(frozen=True, slots=True)
class ConfigRecord:
    """Flattened view of a ``vol_config`` row for API responses."""

    version: int
    config: VolTradingConfig
    updated_at: datetime
    updated_by: str | None
    comment: str | None


def _row_to_record(row: VolConfig) -> ConfigRecord:
    """Deserialize the JSONB payload through Pydantic ; empty dict -> defaults."""
    cfg = VolTradingConfig.model_validate(row.config) if row.config else VolTradingConfig()
    return ConfigRecord(
        version=row.version,
        config=cfg,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        comment=row.comment,
    )


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive merge: ``patch`` wins on conflicts, dicts merge in place."""
    out: dict[str, Any] = dict(base)
    for key, new_value in patch.items():
        if isinstance(new_value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(out[key], new_value)
        else:
            out[key] = new_value
    return out


async def get_current(session: AsyncSession) -> ConfigRecord:
    """Latest config row or version=0 sentinel with Pydantic defaults."""
    row = (
        await session.execute(
            select(VolConfig).order_by(desc(VolConfig.version)).limit(1),
        )
    ).scalar_one_or_none()
    if row is None:
        return ConfigRecord(
            version=0,
            config=VolTradingConfig(),
            updated_at=datetime.now().astimezone(),
            updated_by=None,
            comment="no rows yet, returning pydantic defaults",
        )
    return _row_to_record(row)


async def update(
    session: AsyncSession,
    redis: aioredis.Redis,
    patch: Mapping[str, Any],
    user: str | None = None,
    comment: str | None = None,
) -> ConfigRecord:
    """Deep-merge ``patch`` onto the current config, validate, INSERT new version.

    Raises :class:`pydantic.ValidationError` if the merged payload violates
    the schema. The DB transaction commits only after validation succeeds,
    then the Redis PUBLISH fans the new version out to every subscriber.
    """
    current = await get_current(session)
    merged_raw = _deep_merge(current.config.model_dump(), dict(patch))
    validated = VolTradingConfig.model_validate(merged_raw)

    next_version = (
        await session.execute(select(func.coalesce(func.max(VolConfig.version), 0)))
    ).scalar_one() + 1

    row = VolConfig(
        version=next_version,
        config=validated.model_dump(),
        updated_by=user,
        comment=comment,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    await redis.publish(CONFIG_CHANGED_CHANNEL, str(next_version))
    return _row_to_record(row)


async def history(session: AsyncSession, limit: int = 50) -> list[ConfigRecord]:
    """Latest ``limit`` versions, newest first. For the Settings page audit pane."""
    rows = (
        await session.execute(
            select(VolConfig).order_by(desc(VolConfig.version)).limit(limit),
        )
    ).scalars().all()
    return [_row_to_record(r) for r in rows]


async def revert(
    session: AsyncSession,
    redis: aioredis.Redis,
    target_version: int,
    user: str | None = None,
    comment: str | None = None,
) -> ConfigRecord:
    """Insert a new row whose payload duplicates ``target_version``'s config.

    Never mutates historical rows ; the revert itself becomes a new
    auditable version pointing back via ``comment`` (caller convention).
    """
    target = (
        await session.execute(select(VolConfig).where(VolConfig.version == target_version))
    ).scalar_one_or_none()
    if target is None:
        raise ValueError(f"version {target_version} not found")

    next_version = (
        await session.execute(select(func.coalesce(func.max(VolConfig.version), 0)))
    ).scalar_one() + 1

    row = VolConfig(
        version=next_version,
        config=target.config,
        updated_by=user,
        comment=comment or f"revert to version {target_version}",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    await redis.publish(CONFIG_CHANGED_CHANNEL, str(next_version))
    return _row_to_record(row)


def export_json_schema() -> dict[str, Any]:
    """JSON Schema of :class:`VolTradingConfig` for the RJSF frontend."""
    return VolTradingConfig.model_json_schema()
