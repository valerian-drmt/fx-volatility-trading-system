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


async def spot_fallbacks(redis: Any, cutoff: datetime, symbol: str = "EURUSD") -> tuple[float | None, float | None]:
    if redis is None:
        return None, None
    try:
        raw_spot = await asyncio.wait_for(redis.get(keys.LATEST_SPOT.format(symbol=symbol)), 2.0)
        raw_bars = await asyncio.wait_for(redis.get(keys.BARS.format(symbol=symbol, timeframe="1W")), 2.0)
    except Exception:
        return None, None
    spot_now = float(raw_spot) if raw_spot not in (None, "", b"") else None
    bars = None
    if raw_bars:
        try:
            bars = json.loads(raw_bars)
        except (TypeError, ValueError):
            bars = None
    return spot_now, spot_at_or_before(bars if isinstance(bars, list) else None, cutoff)
