"""Background loop that re-fits the active PCA model on a fixed cadence.

Lives in the api container — same pattern as ``orchestration/events/scheduler``
(plain asyncio.create_task, no APScheduler dependency). Spec §7.1 prescribes
a weekly cron in a dedicated ``pca-fitter`` container ; for the MVP we run
it inline so the api lifespan owns the loop and there's no extra service to
deploy. Migrating to a separate container is a 1-line move (instantiate
``PcaRefitScheduler`` in a thin ``main.py`` instead of in api lifespan).

Behaviour :
  - sleep ``startup_delay_s`` after api boot (avoid refitting during a
    cold-start with empty DB)
  - then run ``perform_refit`` every ``interval_hours`` ± ``jitter_minutes``
  - if not enough hourly snapshots → log and skip, retry next interval
  - any other failure → log and continue (loop never dies)

Defaults are sandbox-friendly (1h interval). For prod, set ``PCA_REFIT_INTERVAL_HOURS``
to ``168`` (= weekly) per spec §7.1.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class PcaRefitScheduler:
    def __init__(
        self,
        sessionmaker_factory: Callable[[], async_sessionmaker[AsyncSession]],
        refit_fn: Callable[[AsyncSession, str], Awaitable[dict[str, Any]]],
        symbol: str = "EURUSD",
        interval_hours: float = 1.0,
        jitter_minutes: float = 5.0,
        startup_delay_s: float = 60.0,
    ):
        self._sm_factory = sessionmaker_factory
        self._refit = refit_fn
        self.symbol = symbol
        self.interval_hours = interval_hours
        self.jitter_minutes = jitter_minutes
        self.startup_delay_s = startup_delay_s
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> dict[str, Any] | None:
        sm = self._sm_factory()
        async with sm() as db:
            try:
                report = await self._refit(db, self.symbol)
                logger.info(
                    "pca_refit_done version=%s prev=%s n_obs=%d cos=%s",
                    report.get("version"), report.get("previous_version"),
                    report.get("n_obs_used", 0),
                    report.get("cosine_similarity"),
                )
                return report
            except Exception as e:
                # 400 (not enough snapshots) is expected during early sandbox
                # life ; log at INFO. Anything else → WARNING.
                msg = str(e)
                if "snapshots" in msg.lower():
                    logger.info("pca_refit_skipped %s", msg)
                else:
                    logger.warning("pca_refit_failed %s: %s", type(e).__name__, msg)
                return None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="pca_refit_scheduler_loop")

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
                logger.exception("pca_refit_scheduler_cycle_crashed")
            jitter_s = random.uniform(-self.jitter_minutes, self.jitter_minutes) * 60
            await asyncio.sleep(self.interval_hours * 3600 + jitter_s)


def build_pca_refit_scheduler() -> PcaRefitScheduler:
    """Wire dependencies + read env-driven config."""
    from api.routers.signals import perform_refit
    from persistence.db import get_sessionmaker

    interval_h = float(os.environ.get("PCA_REFIT_INTERVAL_HOURS", "1.0"))
    jitter_m = float(os.environ.get("PCA_REFIT_JITTER_MIN", "5.0"))
    startup_s = float(os.environ.get("PCA_REFIT_STARTUP_DELAY_S", "60.0"))
    symbol = os.environ.get("PCA_REFIT_SYMBOL", "EURUSD")

    return PcaRefitScheduler(
        sessionmaker_factory=get_sessionmaker,
        refit_fn=perform_refit,
        symbol=symbol,
        interval_hours=interval_h,
        jitter_minutes=jitter_m,
        startup_delay_s=startup_s,
    )
