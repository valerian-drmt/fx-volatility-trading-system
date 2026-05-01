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


async def run() -> None:
    settings = get_settings()
    configure_logging(service_name=settings.SERVICE_NAME or "market_data", level=settings.LOG_LEVEL)

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

    async def _post_connect_subscribe() -> None:
        await _subscribe_ib_ticks(ib, _on_ticker_update, settings.MARKET_SYMBOL)

    engine = MarketDataEngine(
        ib=ib,
        redis=redis,
        symbol=settings.MARKET_SYMBOL,
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_latest_tick=_fetch_latest_tick,
        post_connect_hook=_post_connect_subscribe,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, engine.request_stop)
        except NotImplementedError:
            # Windows asyncio loop does not implement add_signal_handler.
            signal.signal(sig, lambda _s, _f: engine.request_stop())

    await engine.run()


async def _subscribe_ib_ticks(ib: Any, on_update: Any, symbol: str) -> None:
    """Subscribe to the IB Forex ticker for ``symbol`` and fan ticks to ``on_update``.

    Called once after ib is connected (via the engine's post_connect_hook).
    Kept as a dedicated async function so tests can monkeypatch it without
    pulling ib_insync into the test env.

    Uses delayed market data (type 3) so the subscription does not
    conflict with a competing live session (Error 10197) elsewhere
    (native IB Gateway, TWS, mobile app). Delayed data is ~20 min old
    but sufficient for the pipeline smoke.
    """
    from ib_insync import Forex

    try:
        ib.reqMarketDataType(3)
    except Exception:
        pass  # non-fatal, fall through to real-time
    contract = Forex(symbol)
    await ib.qualifyContractsAsync(contract)
    ticker = ib.reqMktData(contract, "", False, False)
    ticker.updateEvent += on_update


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
