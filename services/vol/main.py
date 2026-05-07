"""Entrypoint for the vol-engine container.

Wires shared/ helpers to the ``VolEngine`` class, installs signal
handlers for graceful stop, runs until SIGTERM. The IB-side fetchers
(``fetch_fop_chain`` and ``fetch_ohlc``) remain stubs in this PR — the
full port of the monolith's FOP-chain traversal is deferred to a later
R7 PR to keep the surface bite-sized.
"""
from __future__ import annotations

import asyncio
import signal
from typing import Any

from shared.config import get_settings
from shared.logging import configure_logging
from shared.redis_client import get_async_redis


async def run() -> None:
    settings = get_settings()
    configure_logging(
        service_name=settings.SERVICE_NAME or "vol_engine", level=settings.LOG_LEVEL
    )

    from ib_insync import IB

    from services.vol.engine import VolEngine

    ib = IB()
    redis = get_async_redis()

    def _fop_stub(_F: float) -> dict[str, list[tuple[float, float, float]]]:
        # Real FOP chain traversal lands with PR "vol-engine-fop-chain" in R7.
        return {}

    def _ohlc_stub() -> Any:
        return None

    engine = VolEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_fop_chain=_fop_stub,
        fetch_ohlc=_ohlc_stub,
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
