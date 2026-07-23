"""Entrypoint for the analytics container.

Runs the hourly batch analytics (PCA snapshot, GMM regime refit, PC3 history)
on its own decoupled loop — no IB. Graceful SIGTERM/SIGINT stops the compute
loop and the background heartbeat, then exits.
"""
from __future__ import annotations

import asyncio
import os
import signal

from bus.client import get_async_redis
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
    from persistence.db import get_sessionmaker

    # ANALYTICS_INTERVAL_MIN (default 60) → compute cadence in seconds.
    interval_min = float(os.environ.get("ANALYTICS_INTERVAL_MIN", "60"))
    interval_s = int(interval_min * 60)

    redis = get_async_redis()
    engine = AnalyticsEngine(
        redis=redis,
        symbol="EURUSD",
        interval_s=interval_s,
        sessionmaker=get_sessionmaker(),
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, engine.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _s, _f: engine.request_stop())

    await engine.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
