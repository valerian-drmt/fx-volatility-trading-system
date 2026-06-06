"""FX cash market hours helper.

The cash FX market is closed from Friday 22:00 UTC to Sunday 22:00 UTC.
Engines that should not write garbage rows when no real prices are flowing
gate themselves on :func:`is_fx_market_open`.

Env override ``FORCE_RUN_MARKET_HOURS=1`` keeps cycles running even when
the market is closed — useful for backtest replays or for re-running an
engine against persisted Redis state on a weekend.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime


def is_fx_market_open(now: datetime | None = None) -> bool:
    """True if the FX cash market is currently open.

    Cash hours : Sun 22:00 UTC → Fri 22:00 UTC.
    """
    t = now if now is not None else datetime.now(UTC)
    dow = t.weekday()  # 0 = Mon … 4 = Fri, 5 = Sat, 6 = Sun
    h = t.hour
    if dow == 5:                  # Saturday — closed all day
        return False
    if dow == 6:                  # Sunday — open from 22:00 UTC
        return h >= 22
    if dow == 4:                  # Friday — closed from 22:00 UTC
        return h < 22
    return True                   # Mon–Thu — open round the clock


def market_gate_active() -> bool:
    """Return True when the engine should respect market hours.

    Honours the ``FORCE_RUN_MARKET_HOURS`` env var so an operator can
    re-run engines off-hours for replay / debug.
    """
    if os.environ.get("FORCE_RUN_MARKET_HOURS") in ("1", "true", "True"):
        return False
    return True
