"""Background-task supervision for the engines (#337, audit 06#10).

The error policy, in one place (``docs/architecture/backend.md`` says the
same in prose):

* a **cyclic engine** (market-data, vol, risk, analytics, db-writer) is its
  process: an exception out of the cycle loop ends the process, compose
  restarts the container (``restart: unless-stopped``) and the heartbeat TTL
  raises ``EngineDown`` meanwhile. Inside a cycle, a step failure is logged
  and the cycle goes on.
* the **execution engine** runs several loops next to its HTTP server. A
  loop that dies would leave the server up and the engine silently
  degraded, so every loop runs under ``run_supervised``: an unexpected exit
  is logged with the task name, counted in
  ``engine_task_restarts_total{engine,task}`` and the loop is restarted
  after a capped backoff; cancellation (shutdown) passes through untouched.
  A loop that keeps dying keeps restarting, loudly, instead of disappearing.
* a **one-shot** task (a fill callback, a hedge finaliser) gets
  ``log_task_result`` as its done-callback so its exception reaches the log
  instead of asyncio's "Task exception was never retrieved" at garbage
  collection.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

RESTART_BACKOFF_S: tuple[float, ...] = (1.0, 5.0, 30.0)


def _count_restart(engine: str, task: str) -> None:
    try:
        from shared.observability import engine_task_restarts_total

        engine_task_restarts_total.labels(engine=engine, task=task).inc()
    except Exception:  # metrics are never load-bearing
        pass


async def run_supervised(
    factory: Callable[[], Awaitable[Any]],
    *,
    name: str,
    engine: str,
    restart: bool = True,
    backoff_s: tuple[float, ...] = RESTART_BACKOFF_S,
) -> None:
    """Run ``factory()`` and, if it exits with an exception, log, count and
    run it again after a backoff that grows to the last value of
    ``backoff_s``. A clean return ends the supervision (the loop chose to
    stop); ``CancelledError`` propagates (shutdown)."""
    attempt = 0
    while True:
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            delay = backoff_s[min(attempt, len(backoff_s) - 1)]
            attempt += 1
            _count_restart(engine, name)
            logger.exception(
                "engine_task_crashed", extra={"engine": engine, "task": name, "restart_in_s": delay, "attempt": attempt},
            )
            if not restart:
                return
            await asyncio.sleep(delay)


def spawn_supervised(
    factory: Callable[[], Awaitable[Any]], *, name: str, engine: str, restart: bool = True,
) -> asyncio.Task[None]:
    """``asyncio.create_task`` of :func:`run_supervised`, named."""
    return asyncio.create_task(run_supervised(factory, name=name, engine=engine, restart=restart), name=name)


def log_task_result(task: asyncio.Task[Any], *, engine: str = "engine") -> None:
    """Done-callback for one-shot tasks: an exception is logged with the task
    name; cancellation and clean completion are silent."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "background_task_failed", exc_info=exc,
            extra={"engine": engine, "task": task.get_name()},
        )
