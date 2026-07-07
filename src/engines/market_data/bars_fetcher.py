"""Historical OHLC bars for the EUR/USD spot ticker.

Pulls real candles from IB via ``reqHistoricalDataAsync`` for the timeframes the
frontend ticker offers, and normalises them to plain ``{t, o, h, l, c}`` dicts
(``t`` = bar-open epoch **milliseconds**, UTC). Unlike the vol engine's
``historical_fetcher`` (daily bars on the EUR CONTFUT for RV/GARCH), this uses
the ``Forex("EURUSD")`` spot contract with ``whatToShow="MIDPOINT"`` — the mid
series a chart wants.

The engine caches the result in Redis (``bars:{symbol}:{tf}``) so the API can
serve it without any IB access of its own.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Range preset -> (IB durationStr, IB barSizeSetting). Each button is a
# range/interval pair (like TradingView): day = intraday 15m, week = hourly,
# month = 4h. Candle count differs per range, by design.
TF_SPECS: dict[str, tuple[str, str]] = {
    "1D": ("1 D", "15 mins"),   # the day  — ~96 × 15m
    "1W": ("1 W", "1 hour"),    # the week — ~120 × 1h
    "1M": ("1 M", "4 hours"),   # the month — ~180 × 4h
}


def _to_epoch_ms(d: Any) -> int:
    """IB bar date → epoch ms (UTC). Handles datetime (formatDate=1) and int/str
    epoch seconds (formatDate=2)."""
    if isinstance(d, datetime):
        dt = d if d.tzinfo else d.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    return int(float(d) * 1000)


async def fetch_bars(ib: Any, symbol: str = "EURUSD") -> dict[str, list[dict[str, float]]]:
    """Fetch OHLC bars for every timeframe. Returns ``{tf: [{t,o,h,l,c}, ...]}``
    (ascending by time). A timeframe that errors / returns nothing maps to ``[]``
    so a single bad request never blocks the others."""
    from ib_insync import Forex

    contract = Forex(symbol)
    try:
        await ib.qualifyContractsAsync(contract)
    except Exception:
        logger.exception("qualify_forex_failed", extra={"symbol": symbol})
        return {tf: [] for tf in TF_SPECS}

    out: dict[str, list[dict[str, float]]] = {}
    for tf, (duration, bar_size) in TF_SPECS.items():
        try:
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="MIDPOINT",
                useRTH=False,            # FX trades ~24/5 — keep the full session
                formatDate=2,            # epoch seconds
            )
        except Exception:
            logger.exception("reqHistoricalData_failed", extra={"symbol": symbol, "tf": tf})
            out[tf] = []
            continue
        rows = [
            {
                "t": _to_epoch_ms(b.date),
                "o": float(b.open),
                "h": float(b.high),
                "l": float(b.low),
                "c": float(b.close),
            }
            for b in (bars or [])
        ]
        rows.sort(key=lambda r: r["t"])
        out[tf] = rows
        logger.info("fetch_bars", extra={"symbol": symbol, "tf": tf, "n": len(rows)})
    return out
