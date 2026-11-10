"""Entrypoint for the analytics container.

Runs the hourly batch analytics (PCA snapshot, GMM regime refit, PC3 history)
on its own decoupled loop — no IB. Graceful SIGTERM/SIGINT stops the compute
loop and the background heartbeat, then exits.
"""
from __future__ import annotations

import asyncio
import signal

from bus.client import get_async_redis
from bus.config_watch import ConfigWatcher
from shared.config import get_settings
from shared.logging import configure_logging
from shared.observability import start_metrics_server
from shared.tracing import init_tracing

# P0 obs : Prometheus /metrics endpoint port (9102 vol, 9103 risk, 9104 exec,
# 9105 db-writer are taken).
_METRICS_PORT = 9106


async def run() -> None:
    settings = get_settings()
    configure_logging(
        service_name=settings.SERVICE_NAME or "analytics_engine",
        level=settings.LOG_LEVEL,
    )
    start_metrics_server(_METRICS_PORT, engine="analytics_engine")
    # P2 obs : OTel tracer (rollout post P2.1 validation).
    init_tracing(service_name=settings.SERVICE_NAME or "analytics_engine")

    from engines.analytics.engine import AnalyticsEngine
    from persistence.config_store import load_latest_config
    from persistence.db import get_sessionmaker

    # Cadence, window, snapshot gap, rollup cadence and thread cap come from
    # the capacity dial in the versioned config (#371 / #329): the DB's
    # latest row before the first cycle, every newer version after that.
    redis = get_async_redis()
    sessionmaker = get_sessionmaker()
    engine = AnalyticsEngine(redis=redis, symbol="EURUSD", sessionmaker=sessionmaker)
    watcher = ConfigWatcher(
        redis,
        apply=lambda version, cfg: engine.apply_config(cfg, version),
        loader=lambda: load_latest_config(sessionmaker),
        name="analytics_engine",
    )
    await watcher.load_once()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, engine.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _s, _f: engine.request_stop())

    watcher_task = asyncio.create_task(watcher.run(engine._stop), name="analytics_config_watcher")
    try:
        await engine.run()
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
