"""Entrypoint for the market-data container.

Usage :
    IB_HOST=ib-gateway IB_PORT=4002 IB_CLIENT_ID=1 REDIS_URL=redis://redis:6379/0 \\
        python -m engines.market_data.main

Wires together the pieces :

- ``shared.config.get_settings`` → env-driven config
- ``shared.logging.configure_logging`` → JSON logs with service_name
- ``bus.get_async_redis`` → process-wide Redis pool
- ``ib_insync.IB`` → one connection per service (client_id injected)
- ``engines.market_data.engine.MarketDataEngine`` → the actual loop

Handles SIGTERM gracefully : signals the engine to stop, lets ``run()``
finalise (publish pending heartbeat, disconnect IB), then exits 0. The
container orchestrator sees a clean stop instead of a killed process.
"""
from __future__ import annotations

import asyncio
import signal
from typing import Any

from bus import get_async_redis
from shared.config import get_settings
from shared.logging import configure_logging
from shared.observability import start_metrics_server
from shared.tracing import init_tracing

# P0 obs : Prometheus /metrics endpoint port. Spec § Phase 0 step 3.
_METRICS_PORT = 9101


async def run() -> None:
    settings = get_settings()
    configure_logging(service_name=settings.SERVICE_NAME or "market_data", level=settings.LOG_LEVEL)
    start_metrics_server(_METRICS_PORT, engine="market_data")
    # P2 obs : OTel tracer (rollout post P2.1 validation).
    init_tracing(service_name=settings.SERVICE_NAME or "market_data")

    # ib_insync import is deferred so the unit tests don't need the dep tree.
    from ib_insync import IB

    ib = IB()
    redis = get_async_redis()

    from engines.market_data.engine import MarketDataEngine

    # In the monolith, the engine pulled ticks from a ``Ticker`` event queue ;
    # here we delegate that plumbing to a small helper defined below so the
    # engine class stays agnostic of ib_insync types.
    latest: dict[str, Any] = {}

    def _on_ticker_update(tick: Any) -> None:
        latest.update(
            {
                "bid": getattr(tick, "bid", None),
                "ask": getattr(tick, "ask", None),
                "mid": (getattr(tick, "bid", 0) + getattr(tick, "ask", 0)) / 2
                if getattr(tick, "bid", None) and getattr(tick, "ask", None)
                else None,
            }
        )

    def _fetch_latest_tick() -> dict[str, Any] | None:
        return latest or None

    engine = MarketDataEngine(
        ib=ib,
        redis=redis,
        symbol=settings.IB_HOST,  # symbol env is added in a later PR ; placeholder
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_latest_tick=_fetch_latest_tick,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, engine.request_stop)
        except NotImplementedError:
            # Windows asyncio loop does not implement add_signal_handler.
            signal.signal(sig, lambda _s, _f: engine.request_stop())

    _subscribe_ib_ticks(ib, _on_ticker_update)
    await engine.run()


def _subscribe_ib_ticks(ib: Any, on_update: Any) -> None:
    """Stub — wiring the IB Forex ticker event is added in a later PR.

    Kept as a dedicated function so the unit tests can monkeypatch it out
    entirely without pulling ib_insync into the test env.
    """


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
