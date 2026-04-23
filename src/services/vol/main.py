"""Entrypoint for the vol-engine container.

SANDBOX NOTE (sandbox/r9-pipeline-verif) : ``fetch_fop_chain`` now wires
through ``services.vol.chain_fetcher.scan_all_tenors_concurrent``, a
real async port of the monolith's FOP traversal with bounded
parallelism (Semaphore). ``fetch_ohlc`` remains a synthetic stub until
the historical bar fetch is ported.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from redis import asyncio as aioredis

from bus.channels import CH_CONFIG_CHANGED
from shared.config import get_settings
from shared.logging import configure_logging
from shared.redis_client import get_async_redis

logger = logging.getLogger(__name__)


async def _load_config_from_db() -> Any | None:
    """Fetch the latest VolTradingConfig from Postgres, or None on any error.

    Non-fatal : when DATABASE_URL is unset or Postgres is unreachable the
    vol-engine keeps the env-var defaults provided by pydantic-settings.
    """
    try:
        from api.services import config_service
        from persistence.db import get_session

        async with get_session() as session:
            record = await config_service.get_current(session)
            return record.config
    except Exception as exc:
        logger.warning("vol_engine_db_config_unavailable reason=%s", exc)
        return None


async def _watch_config_changes(
    redis: aioredis.Redis, engine: Any, stop: asyncio.Event,
) -> None:
    """Subscribe to CH_CONFIG_CHANGED and hot-reload the engine on each event."""
    try:
        pubsub = redis.pubsub()
        await pubsub.subscribe(CH_CONFIG_CHANGED)
    except Exception as exc:
        logger.warning("vol_engine_config_watcher_subscribe_failed reason=%s", exc)
        return

    try:
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=2.0,
                )
            except TimeoutError:
                continue
            if msg is None or msg.get("type") != "message":
                continue
            cfg = await _load_config_from_db()
            if cfg is not None:
                engine.apply_config(cfg)
    finally:
        try:
            await pubsub.unsubscribe(CH_CONFIG_CHANGED)
            await pubsub.close()
        except Exception:
            pass


async def run() -> None:
    settings = get_settings()
    configure_logging(
        service_name=settings.SERVICE_NAME or "vol_engine", level=settings.LOG_LEVEL
    )

    from ib_insync import IB

    from services.vol.engine import VolEngine

    ib = IB()
    redis = get_async_redis()

    # Cache the discovered chains — they only change when IB rolls expiries.
    _chains_cache: dict[str, Any] = {"chains": None, "delayed_enabled": False}

    async def _fop_real(F: float) -> dict[str, list[tuple[float, float, float]]]:
        """Real FOP scan on IB : discover chains once, then scan in parallel."""
        from services.vol import chain_fetcher

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
        return await chain_fetcher.scan_all_tenors_concurrent(ib, F, chains)

    async def _ohlc_real() -> Any:
        """Real IB daily bars for EUR CONTFUT — cached 30min inside the fetcher."""
        from services.vol import historical_fetcher

        return await historical_fetcher.fetch_daily_ohlc(ib, duration_str="1 Y")

    engine = VolEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_fop_chain=_fop_real,
        fetch_ohlc=_ohlc_real,
        signal_threshold_vol_pts=settings.THRESHOLD_VOL_PTS,
        signal_model_p=settings.MODEL_P,
    )

    # Boot-time config load : DB wins over env vars when available.
    db_cfg = await _load_config_from_db()
    if db_cfg is not None:
        engine.apply_config(db_cfg)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, engine.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _s, _f: engine.request_stop())

    watcher = asyncio.create_task(_watch_config_changes(redis, engine, engine._stop))
    try:
        await engine.run()
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):
            pass


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
