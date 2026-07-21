"""Async port of the monolith's _fetch_ohlc : daily bars for EUR FUT.

Yang-Zhang RV and GARCH both need a clean OHLC series to converge to
realistic values. The monolith (src/engines/vol_engine.py::_fetch_ohlc)
pulled ~1 year of daily bars on the continuous EUR FUT contract via
``reqHistoricalData``. R7 services/vol shipped with a sandbox stub
returning 20 synthetic random-walk bars, which made RV and GARCH come
out at ~2.5% against a real implied of 6% — all signals EXPENSIVE.

This module reproduces the v1 call in async form and wraps it with a
30-minute in-memory cache so the engine fetches at most twice per hour
(vol cycle is 30s — without the cache IB would throttle us).

Public API
----------
``fetch_daily_ohlc(ib, duration_str="1 Y") -> pandas.DataFrame | None``

Returns a DataFrame with columns ``date, open, high, low, close`` sorted
ascending. ``None`` when the IB session is unauthorised or the farm is
disconnected ; callers should treat that as "no RV this cycle".
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Cache lifetime (seconds). Daily bars change once per day, so 30min is
# conservative — a longer TTL is fine but we keep it short so the first
# cycle after a market-moving day picks up the fresh bar without a restart.
CACHE_TTL_S: int = 30 * 60

_cache: dict[str, Any] = {"df": None, "ts": 0.0, "key": ""}


async def fetch_daily_ohlc(
    ib: Any,
    duration_str: str = "1 Y",
    contract_symbol: str = "EUR",
    contract_exchange: str = "CME",
    contract_currency: str = "USD",
) -> Any | None:
    """Fetch `duration_str` of daily bars on the EUR continuous future.

    Returns a pandas.DataFrame with ``open/high/low/close`` columns, or
    ``None`` if IB returned no bars. Cached for ``CACHE_TTL_S`` seconds.
    """
    import pandas as pd

    cache_key = f"{contract_symbol}/{contract_exchange}/{contract_currency}/{duration_str}"
    now = time.monotonic()
    if (
        _cache["df"] is not None
        and _cache["key"] == cache_key
        and now - _cache["ts"] < CACHE_TTL_S
    ):
        return _cache["df"]

    from ib_insync import Contract

    cont_fut = Contract(
        symbol=contract_symbol,
        secType="CONTFUT",
        exchange=contract_exchange,
        currency=contract_currency,
    )
    try:
        # whatToShow="TRADES": the only mode IB supports for FUT/CONTFUT.
        # ADJUSTED_LAST only exists for STK (split/dividend adjusted);
        # on a futures it triggers Error 162 "API historical data query
        # cancelled" and `bars` comes back empty.
        bars = await ib.reqHistoricalDataAsync(
            cont_fut,
            endDateTime="",
            durationStr=duration_str,
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
    except Exception:
        logger.exception("reqHistoricalDataAsync_failed")
        return None
    if not bars:
        logger.info("fetch_daily_ohlc: IB returned no bars", extra={"key": cache_key})
        return None

    df = pd.DataFrame([
        {"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
        for b in bars
    ])
    df = df.sort_values("date").reset_index(drop=True)
    _cache["df"] = df
    _cache["ts"] = now
    _cache["key"] = cache_key
    logger.info(
        "fetch_daily_ohlc: %d bars cached (duration=%s)",
        len(df), duration_str,
    )
    return df


def reset_cache() -> None:
    """Clear the module-level cache. Used by tests and on re-connect."""
    _cache["df"] = None
    _cache["ts"] = 0.0
    _cache["key"] = ""
