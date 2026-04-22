"""Entrypoint for the vol-engine container.

SANDBOX NOTE (sandbox/r9-pipeline-verif) : the original R7 code shipped
empty stubs for ``fetch_fop_chain`` / ``fetch_ohlc`` with a comment
"real traversal lands in a later PR". To validate the end-to-end pipe
(vol-engine → Redis → API WS → frontend) without waiting for the real
FOP chain implementation, the stubs here return **synthetic but
realistic** data. Do NOT promote this file into an official PR — replace
with the real FOP chain traversal before merging.
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

    def _fop_sandbox(F: float) -> dict[str, list[tuple[float, float, float]]]:
        """Synthetic FX smile : realistic term structure + convex skew."""
        # (delta, iv_offset_from_atm) — typical EURUSD smile shape.
        points = [
            (0.10, +0.010),   # 10dc — wing call
            (0.25, +0.003),   # 25dc
            (0.50, +0.000),   # atm
            (0.75, +0.002),   # 25dp
            (0.90, +0.008),   # 10dp — wing put
        ]
        # ATM vol per tenor (upward term structure — long-dated > short-dated).
        atm_by_tenor = {"1W": 0.065, "1M": 0.072, "3M": 0.080}
        out: dict[str, list[tuple[float, float, float]]] = {}
        for tenor, atm_iv in atm_by_tenor.items():
            obs: list[tuple[float, float, float]] = []
            for delta, iv_bump in points:
                iv = atm_iv + iv_bump
                # Approximate strike : K = F * (1 + shift) where shift ~ (0.5 - delta) * iv.
                strike = F * (1.0 + (0.5 - delta) * iv)
                obs.append((delta, iv, strike))
            out[tenor] = obs
        return out

    def _ohlc_sandbox() -> Any:
        """Synthetic OHLC history (20 bars) seeded around a typical EURUSD level."""
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(seed=42)
        n = 20
        drift = rng.normal(0.0, 0.002, n).cumsum()
        close = 1.17 + drift
        open_ = close + rng.normal(0.0, 0.0006, n)
        high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 0.0008, n))
        low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 0.0008, n))
        return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})

    engine = VolEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host=settings.IB_HOST,
        ib_port=settings.IB_PORT,
        client_id=settings.IB_CLIENT_ID,
        fetch_fop_chain=_fop_sandbox,
        fetch_ohlc=_ohlc_sandbox,
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
