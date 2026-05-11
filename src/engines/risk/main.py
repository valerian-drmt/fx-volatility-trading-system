"""Entrypoint for the risk container."""
from __future__ import annotations

import asyncio
import signal

from bus import get_async_redis
from shared.config import get_settings
from shared.logging import configure_logging
from shared.observability import start_metrics_server
from shared.tracing import init_tracing

# P0 obs : Prometheus /metrics endpoint port. Spec § Phase 0 step 3.
_METRICS_PORT = 9103


async def run() -> None:
    settings = get_settings()
    configure_logging(
        service_name=settings.SERVICE_NAME or "risk_engine", level=settings.LOG_LEVEL
    )
    start_metrics_server(_METRICS_PORT)
    # P2 obs : OTel tracer (rollout post P2.1 validation).
    init_tracing(service_name=settings.SERVICE_NAME or "risk_engine")

    from ib_insync import IB

    from engines.risk.engine import RiskEngine
    from persistence.db import get_sessionmaker

    ib = IB()
    redis = get_async_redis()
    sessionmaker = get_sessionmaker()

    engine = RiskEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        sessionmaker=sessionmaker,
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
