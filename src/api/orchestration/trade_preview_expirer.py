"""Background loop : flip ``trade_previews`` rows whose ``expires_at`` < now()
and ``state in (valid_for_submit, blocked)`` to ``state='expired'``.

Lightweight (cheap query, ~30s interval). Runs in api lifespan.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.models import TradePreviewRow

logger = logging.getLogger(__name__)


class TradePreviewExpirer:
    def __init__(
        self,
        sessionmaker_factory,
        interval_s: float = 30.0,
        startup_delay_s: float = 30.0,
    ):
        self._sm_factory = sessionmaker_factory
        self.interval_s = interval_s
        self.startup_delay_s = startup_delay_s
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        sm: async_sessionmaker[AsyncSession] = self._sm_factory()
        async with sm() as db:
            now = datetime.now(UTC)
            result = await db.execute(
                update(TradePreviewRow)
                .where(TradePreviewRow.expires_at < now)
                .where(TradePreviewRow.state.in_(("valid_for_submit", "blocked")))
                .values(state="expired")
            )
            await db.commit()
            count = result.rowcount or 0
            if count:
                logger.info("trade_previews_expired %d rows", count)
            return count

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="trade_preview_expirer")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        await asyncio.sleep(self.startup_delay_s)
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("trade_preview_expirer_cycle_crashed")
            await asyncio.sleep(self.interval_s)


def build_trade_preview_expirer() -> TradePreviewExpirer:
    from persistence.db import get_sessionmaker
    return TradePreviewExpirer(sessionmaker_factory=get_sessionmaker)
