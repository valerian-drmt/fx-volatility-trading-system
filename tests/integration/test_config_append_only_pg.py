"""config_vol_engine is append-only in Postgres (#329): the trigger of
migration 062 refuses UPDATE and DELETE, INSERT still works."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [pytest.mark.db_integration, pytest.mark.asyncio]


async def test_update_and_delete_are_refused_insert_is_not() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url or not os.environ.get("DB_RUN_INTEGRATION"):
        pytest.skip("db_integration: set DB_RUN_INTEGRATION=1 and DATABASE_URL")
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO config_vol_engine (version, config, updated_by, comment) "
                                    "VALUES (999001, '{}'::jsonb, 'test', 'append-only probe')"))
        for stmt in (
            "UPDATE config_vol_engine SET comment = 'rewritten' WHERE version = 999001",
            "DELETE FROM config_vol_engine WHERE version = 999001",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
        async with engine.connect() as conn:
            n = (await conn.execute(text("SELECT count(*) FROM config_vol_engine WHERE version = 999001"))).scalar_one()
        assert n == 1, "the row survived both refusals"
    finally:
        # The probe row stays: the table is append-only by design. 999001 is far
        # above any real version and the api reads max(version), so a leftover
        # probe would become the live config: disable the trigger to remove it.
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE config_vol_engine DISABLE TRIGGER config_vol_engine_append_only"))
            await conn.execute(text("DELETE FROM config_vol_engine WHERE version = 999001"))
            await conn.execute(text("ALTER TABLE config_vol_engine ENABLE TRIGGER config_vol_engine_append_only"))
        await engine.dispose()
