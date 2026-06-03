"""Entrypoint for the risk-engine container."""
from __future__ import annotations

import asyncio
import signal

from bus import get_async_redis
from shared.config import get_settings
from shared.logging import configure_logging


async def run() -> None:
    settings = get_settings()
    configure_logging(
        service_name=settings.SERVICE_NAME or "risk_engine", level=settings.LOG_LEVEL
    )

    from ib_insync import IB

    from engines.risk.engine import RiskEngine

    ib = IB()
    redis = get_async_redis()

    def _positions_stub() -> list[dict]:
        # Real position fetch (IB account + local book) lands in a later R7 PR.
        return []

    engine = RiskEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_positions=_positions_stub,
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
