"""Weekly batch : recompute ``vol_features_context_baseline``.

Same shape as ``PcaRefitScheduler`` — owns its own ``asyncio.Task``, sleeps
until the next Sunday 00:00 UTC, then calls ``compute_baseline`` and goes
back to sleep. ``run_once`` is exposed for the operator to trigger manually
via ``POST /api/v1/regime/baseline/run-once`` (cf. router patch).

Cf. spec E3 §"context baseline batch + cron weekly Sunday 00:00 UTC".
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def _seconds_until_next_sunday_midnight(now: datetime) -> float:
    """Return the wait-time in seconds until the next Sunday 00:00 UTC.

    Python ISO weekday : Monday=1, Sunday=7. We sleep at most one week.
    """
    days_to_sunday = (6 - now.weekday()) % 7  # Monday=0 ... Sunday=6
    if days_to_sunday == 0 and (now.hour, now.minute, now.second) >= (0, 0, 0):
        # Already past Sunday 00:00 — schedule for next Sunday.
        days_to_sunday = 7
    target = (now + timedelta(days=days_to_sunday)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return max(60.0, (target - now).total_seconds())


class BaselineScheduler:
    """Sleeps until next Sunday 00:00 UTC, then recomputes the baseline."""

    def __init__(
        self,
        sessionmaker_factory: Callable[[], async_sessionmaker[AsyncSession]],
        *,
        startup_delay_s: float = 60.0,
    ):
        self._sm_factory = sessionmaker_factory
        self.startup_delay_s = startup_delay_s
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> dict[str, Any] | None:
        """Trigger a recompute now ; return the report dict."""
        from api.orchestration.baseline_compute import compute_baseline
        sm = self._sm_factory()
        try:
            async with sm() as db:
                report = await compute_baseline(db)
            logger.info(
                "baseline_recompute_done valid=%s insufficient=%s total=%s",
                report["valid"], report["insufficient"], report["total_cells"],
            )
            return report
        except Exception:
            logger.exception("baseline_recompute_failed")
            return None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="baseline_scheduler_loop")

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
        # First run on boot — handy when the table is empty.
        await self.run_once()
        while True:
            try:
                wait = _seconds_until_next_sunday_midnight(datetime.now(UTC))
                logger.info("baseline_scheduler_sleeping seconds=%.0f", wait)
                await asyncio.sleep(wait)
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("baseline_scheduler_cycle_crashed")
                await asyncio.sleep(3600)


def build_baseline_scheduler() -> BaselineScheduler:
    """Wire dependencies for the api lifespan."""
    from persistence.db import get_sessionmaker
    startup = float(os.environ.get("BASELINE_SCHEDULER_STARTUP_S", "60.0"))
    return BaselineScheduler(
        sessionmaker_factory=get_sessionmaker, startup_delay_s=startup,
    )
