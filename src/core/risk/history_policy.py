"""When a per-position greeks snapshot is worth a row (#373, hot/cold T9).

The risk engine used to append one ``open_position_history`` row per open
position every 30 s, whether or not anything had changed: a flat weekend
book wrote the same numbers 2 880 times a day per leg. The readers
(``pnl-attribution``, ``marginal-var``, the per-day rollups) need the
*changes* at minute resolution and a heartbeat row now and then, not a
copy per cycle. A row is written when the greeks or the P&L moved beyond
a threshold since the last row, or when the last row is older than the
heartbeat floor, so a quiet position still has a recent snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass

# Minimum movement that earns a new row. Relative on the greeks (1 % of the
# previous value) with an absolute floor so a tiny position still moves, and
# absolute on the P&L and the IV.
REL_MOVE = 0.01
ABS_MOVE_USD = 1.0
PNL_MOVE_USD = 1.0
IV_MOVE = 0.0005          # 0.05 vol pt
HEARTBEAT_S = 900.0       # at least one row per position every 15 min


@dataclass(frozen=True)
class GreeksPoint:
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    pnl: float | None = None
    iv: float | None = None
    mark: float | None = None


def _moved(prev: float | None, new: float | None, *, rel: float, floor: float) -> bool:
    if prev is None and new is None:
        return False
    if prev is None or new is None:
        return True
    return abs(new - prev) > max(floor, rel * abs(prev))


def snapshot_due(
    prev: GreeksPoint | None,
    new: GreeksPoint,
    *,
    last_written_age_s: float | None,
    heartbeat_s: float = HEARTBEAT_S,
) -> bool:
    """True when ``new`` deserves a history row.

    ``prev`` is the last point written for the position (None = never, so the
    first pass after a restart writes every position once);
    ``last_written_age_s`` is how long ago that was.
    """
    if prev is None or last_written_age_s is None:
        return True
    if last_written_age_s >= heartbeat_s:
        return True
    for name in ("delta", "gamma", "vega", "theta"):
        if _moved(getattr(prev, name), getattr(new, name), rel=REL_MOVE, floor=ABS_MOVE_USD):
            return True
    if _moved(prev.pnl, new.pnl, rel=0.0, floor=PNL_MOVE_USD):
        return True
    if _moved(prev.iv, new.iv, rel=0.0, floor=IV_MOVE):
        return True
    return False
