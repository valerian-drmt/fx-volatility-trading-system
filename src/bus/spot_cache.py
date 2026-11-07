"""Spot reads from the market-data engine's Redis cache (#368).

``spot_fallbacks`` gives the P&L attribution a dS when no open 6E future
carries a mark / a t-1 snapshot: ``latest_spot`` for now, the hourly 1W bar
cache for then. Spot, not the future's forward, but the basis is stable over
a day and dS is what the delta / gamma terms need. Best effort: any Redis
trouble reads as ``(None, None)``. Shared by the api endpoint fallback and the
analytics engine's rollup.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from bus import keys
from core.risk.attribution import spot_at_or_before


def parse_spot(raw: Any) -> tuple[float | None, str | None]:
    """``(value, ts)`` from a ``latest_spot`` / ``latest_bid`` / ``latest_ask``
    value. Accepts the ``{"value", "ts"}`` envelope (#327), the bare float
    string the older market-data engine wrote, and the legacy ``{"mid",
    "bid"}`` dict. Non-positive values are IB's "no quote" sentinel: None."""
    if raw in (None, "", b""):
        return None, None
    text = raw.decode() if isinstance(raw, bytes) else raw
    value: Any = None
    ts: str | None = None
    if isinstance(text, (int, float)):
        value = text
    else:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = text
        if isinstance(payload, dict):
            value = payload.get("value")
            if value is None:
                value = payload.get("mid") or payload.get("bid")
            ts = payload.get("ts") or payload.get("timestamp")
        else:
            value = payload
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None, ts
    return (f if f > 0 else None), ts


async def read_spot(redis: Any, symbol: str = "EURUSD", timeout_s: float = 2.0) -> float | None:
    """The latest spot for ``symbol`` from the cache, or None."""
    if redis is None:
        return None
    try:
        raw = await asyncio.wait_for(redis.get(keys.LATEST_SPOT.format(symbol=symbol)), timeout_s)
    except Exception:
        return None
    return parse_spot(raw)[0]


async def spot_fallbacks(redis: Any, cutoff: datetime, symbol: str = "EURUSD") -> tuple[float | None, float | None]:
    if redis is None:
        return None, None
    try:
        raw_spot = await asyncio.wait_for(redis.get(keys.LATEST_SPOT.format(symbol=symbol)), 2.0)
        raw_bars = await asyncio.wait_for(redis.get(keys.BARS.format(symbol=symbol, timeframe="1W")), 2.0)
    except Exception:
        return None, None
    spot_now = parse_spot(raw_spot)[0]
    bars = None
    if raw_bars:
        try:
            bars = json.loads(raw_bars)
        except (TypeError, ValueError):
            bars = None
    return spot_now, spot_at_or_before(bars if isinstance(bars, list) else None, cutoff)
