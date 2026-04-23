"""Entrypoint for the vol-engine container.

SANDBOX NOTE (sandbox/r9-pipeline-verif) : ``fetch_fop_chain`` now wires
through ``services.vol.chain_fetcher.scan_all_tenors_concurrent``, a
real async port of the monolith's FOP traversal with bounded
parallelism (Semaphore). ``fetch_ohlc`` remains a synthetic stub until
the historical bar fetch is ported.
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

    signal_cfg = _load_signal_config()
    engine = VolEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_fop_chain=_fop_real,
        fetch_ohlc=_ohlc_real,
        signal_threshold_vol_pts=signal_cfg["threshold"],
        signal_model_p=signal_cfg["model_p"],
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, engine.request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda _s, _f: engine.request_stop())

    await engine.run()


def _load_signal_config() -> dict[str, Any]:
    """Read ``config/vol_config.json`` section 'signal' with sane defaults.

    Defaults back to threshold=1.0 / model='har' if the file or section
    is missing. Docker mount path is ``/app/config`` ; dev runs against
    the repo root.
    """
    import json
    import os

    default = {"threshold": 1.0, "model_p": "har"}
    for candidate in ("/app/config/vol_config.json", "config/vol_config.json"):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                cfg = json.load(f)
            sig = cfg.get("signal") or {}
            return {
                "threshold": float(sig.get("THRESHOLD_VOL_PTS", default["threshold"])),
                "model_p": str(sig.get("MODEL_P", default["model_p"])).lower(),
            }
        except Exception:
            continue
    return default


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
