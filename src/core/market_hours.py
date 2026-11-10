"""FX cash market hours, the pure rule (#332): Sunday 22:00 UTC to Friday
22:00 UTC. ``now`` is a parameter, never read here; ``shared.market_hours``
supplies the clock and the operator override for the engines, and
``frontend/src/utils/marketHours.ts`` mirrors the same rule for the desk."""
from __future__ import annotations

from datetime import datetime


def is_fx_market_open(now: datetime) -> bool:
    """True if the FX cash market is open at ``now`` (tz-aware, read in UTC)."""
    t = now
    dow = t.weekday()  # 0 = Mon … 4 = Fri, 5 = Sat, 6 = Sun
    h = t.hour
    if dow == 5:                  # Saturday — closed all day
        return False
    if dow == 6:                  # Sunday — open from 22:00 UTC
        return h >= 22
    if dow == 4:                  # Friday — closed from 22:00 UTC
        return h < 22
    return True                   # Mon–Thu — open round the clock
