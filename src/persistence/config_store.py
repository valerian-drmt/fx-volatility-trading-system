"""Read side of the versioned config for the engines (#329).

The api writes ``config_vol_engine`` (``api.orchestration.config_service``);
the engines read the latest row here at start, on every resubscribe and on
a timer through ``bus.config_watch.ConfigWatcher``. No row yet = version 0
with the Pydantic defaults, the same sentinel the api serves.
"""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import VolTradingConfig
from persistence.models import VolConfig


async def load_latest_config(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[int, VolTradingConfig]:
    async with sessionmaker() as db:
        row = (
            await db.execute(select(VolConfig).order_by(desc(VolConfig.version)).limit(1))
        ).scalar_one_or_none()
    if row is None:
        return 0, VolTradingConfig()
    cfg = VolTradingConfig.model_validate(row.config) if row.config else VolTradingConfig()
    return int(row.version), cfg
