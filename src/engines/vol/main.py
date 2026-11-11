"""Entrypoint for the vol-engine container.

The chain fetch goes through ``engines.vol.chain_fetcher`` (bounded tenor
parallelism, width and concurrency from the capacity dial); the daily bars
through ``engines.vol.historical_fetcher``. The versioned config reaches the
engine through ``bus.config_watch.ConfigWatcher`` (#329): the DB's latest row
at start, every newer ``config:changed`` after that, a re-read on every
resubscribe and on a timer.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from bus.client import get_async_redis
from bus.config_watch import ConfigWatcher
from shared.config import get_settings
from shared.logging import configure_logging
from shared.observability import start_metrics_server
from shared.tracing import init_tracing

logger = logging.getLogger(__name__)

# P0 obs : Prometheus /metrics endpoint port. Spec § Phase 0 step 3.
_METRICS_PORT = 9102


async def run() -> None:
    settings = get_settings()
    configure_logging(
        service_name=settings.SERVICE_NAME or "vol_engine", level=settings.LOG_LEVEL
    )
    start_metrics_server(_METRICS_PORT, engine="vol_engine")
    # P2 obs (PoC on vol-engine only): OTel tracer → otel-collector → Tempo.
    # Spec § Phase 2 gate 2.1 — rollout to the 4 other engines after
    # validating the flame graph in Grafana.
    init_tracing(service_name=settings.SERVICE_NAME or "vol_engine")

    from ib_async import IB

    from engines.vol.engine import VolEngine

    ib = IB()
    redis = get_async_redis()

    # Cache the discovered chains — they only change when IB rolls expiries.
    _chains_cache: dict[str, Any] = {"chains": None, "delayed_enabled": False}

    async def _fop_real(F: float) -> dict[str, list[tuple[float, float, float]]]:
        """Real FOP scan on IB : discover chains once, then scan in parallel."""
        from engines.vol import chain_fetcher

        # Delayed market data (type 3) is required on paper accounts
        # without a live CME entitlement — otherwise modelGreeks stays
        # empty and every tenor drops with 0 usable strikes.
        if not _chains_cache["delayed_enabled"]:
            chain_fetcher.ensure_delayed_market_data(ib)
            _chains_cache["delayed_enabled"] = True
        if _chains_cache["chains"] is None:
            _chains_cache["chains"] = await chain_fetcher.discover_chains(ib)
        chains = _chains_cache["chains"]
        if not chains:
            return {}
        # Width and concurrency from the capacity dial, read on every fetch
        # (#372): a Settings change applies on the next cycle.
        return await chain_fetcher.scan_all_tenors_concurrent(
            ib, F, chains,
            max_concurrent=engine.capacity.max_concurrent_scans,
            n_side=engine.capacity.strikes_per_side,
        )

    async def _ohlc_real() -> Any | None:
        """~1y of daily bars on the EUR continuous future (cached 30min).

        Feeds the fair-vol pipeline (Yang-Zhang RV → HAR/GARCH → +VRP → σ_fair^Q).
        Returns None when IB has no history → the engine simply skips fair vol.
        """
        from engines.vol.historical_fetcher import fetch_daily_ohlc

        return await fetch_daily_ohlc(ib)

    async def _on_ib_reconnected() -> None:
        # Both flags are per-IB-session state : delayed market data (type 3)
        # must be re-armed after the nightly Gateway restart, and the chains
        # are rediscovered (expiries may have rolled while disconnected).
        _chains_cache["delayed_enabled"] = False
        _chains_cache["chains"] = None

    engine = VolEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_fop_chain=_fop_real,
        fetch_ohlc=_ohlc_real,
        on_ib_reconnected=_on_ib_reconnected,
    )

    # Versioned config (#329): the DB's latest row before the first cycle,
    # then every newer version over config:changed; older versions are
    # ignored, a dropped subscription is retried, a timer re-reads the DB.
    from persistence.config_store import load_latest_config
    from persistence.db import get_sessionmaker

    sessionmaker = get_sessionmaker()
    watcher = ConfigWatcher(
        redis,
        apply=lambda version, cfg: engine.apply_config(cfg, version),
        loader=lambda: load_latest_config(sessionmaker),
        name="vol_engine",
    )
    await watcher.load_once()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, engine.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _s, _f: engine.request_stop())

    watcher_task = asyncio.create_task(watcher.run(engine._stop), name="vol_config_watcher")
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
