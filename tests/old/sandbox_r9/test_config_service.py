"""Unit tests for api.orchestration.config_service : versioning, merge, pub/sub."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.orchestration import config_service
from api.orchestration.config_service import CONFIG_CHANGED_CHANNEL
from core.config import VolTradingConfig
from persistence.models import Base


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def redis_mock() -> AsyncMock:
    r = AsyncMock()
    r.publish = AsyncMock(return_value=1)
    return r


async def test_get_current_returns_defaults_when_empty(session):
    rec = await config_service.get_current(session)
    assert rec.version == 0
    assert rec.config == VolTradingConfig()


async def test_update_inserts_new_version_and_publishes(session, redis_mock):
    rec1 = await config_service.update(
        session, redis_mock,
        patch={"signal": {"threshold_vol_pts": 2.5}},
        user="valerian", comment="tighter threshold",
    )
    assert rec1.version == 1
    assert rec1.config.signal.threshold_vol_pts == 2.5
    assert rec1.config.signal.model_p == "har"  # default kept
    redis_mock.publish.assert_awaited_once_with(CONFIG_CHANGED_CHANNEL, "1")

    rec2 = await config_service.update(
        session, redis_mock,
        patch={"sizing": {"base_size": 20}},
        user="valerian",
    )
    assert rec2.version == 2
    assert rec2.config.signal.threshold_vol_pts == 2.5  # preserved from v1
    assert rec2.config.sizing.base_size == 20


async def test_update_rejects_out_of_bounds(session, redis_mock):
    with pytest.raises(ValidationError):
        await config_service.update(
            session, redis_mock,
            patch={"signal": {"threshold_vol_pts": 999.0}},
            user="valerian",
        )
    # DB must remain empty — no partial insert
    rec = await config_service.get_current(session)
    assert rec.version == 0


async def test_history_returns_latest_first(session, redis_mock):
    for n in range(1, 4):
        await config_service.update(
            session, redis_mock,
            patch={"sizing": {"base_size": 10 + n}},
            user="valerian", comment=f"step {n}",
        )
    hist = await config_service.history(session, limit=10)
    assert [r.version for r in hist] == [3, 2, 1]
    assert hist[0].config.sizing.base_size == 13


async def test_revert_inserts_copy_not_update(session, redis_mock):
    await config_service.update(session, redis_mock, patch={"sizing": {"base_size": 50}})
    await config_service.update(session, redis_mock, patch={"sizing": {"base_size": 99}})

    rec = await config_service.revert(session, redis_mock, target_version=1, user="valerian")
    assert rec.version == 3
    assert rec.config.sizing.base_size == 50
    assert "revert to version 1" in (rec.comment or "")

    hist = await config_service.history(session)
    assert [r.version for r in hist] == [3, 2, 1]  # v1 and v2 preserved


async def test_revert_unknown_version_raises(session, redis_mock):
    with pytest.raises(ValueError, match="version 42 not found"):
        await config_service.revert(session, redis_mock, target_version=42)


def test_export_json_schema_has_all_sections():
    schema = config_service.export_json_schema()
    assert "properties" in schema
    for section in ("regime", "signal", "sizing", "exit_rules",
                    "surface", "calibration", "delta_hedge", "structures"):
        assert section in schema["properties"]


def test_deep_merge_nested_dicts():
    from api.orchestration.config_service import _deep_merge
    base = {"a": {"x": 1, "y": 2}, "b": 0}
    patch = {"a": {"y": 20, "z": 3}}
    assert _deep_merge(base, patch) == {"a": {"x": 1, "y": 20, "z": 3}, "b": 0}
