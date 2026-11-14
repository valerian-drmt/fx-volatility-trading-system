"""The price grid of each contract, in one place (#492).

IB refuses a limit that is not on the contract's grid (warning 110). The
option tick was a constant copied in the api and in the engine, and two
limits were only rounded to six decimals: the one the operator types on a
close, and the automatic limit of a future closed outside trading hours.

CME EUR/USD, per the contract specifications:

* options on EUR/USD futures (weekly and monthly): 0.0001;
* 6E future: 0.00005;
* M6E (micro) and E7 (E-mini) futures: 0.0001.

A future this table does not know gets 0.0001, which is on the grid of every
contract above.
"""
from __future__ import annotations

import math

OPTION_TICK = 0.0001
_FUTURE_TICKS = {"6E": 0.00005, "M6E": 0.0001, "E7": 0.0001}
_DEFAULT_TICK = 0.0001


def tick_for(local_symbol: str | None, *, is_option: bool) -> float:
    """Tick of the contract a local symbol names ("6EZ6", "M6EH7", "EUUG7 C1110")."""
    if is_option:
        return OPTION_TICK
    sym = (local_symbol or "").strip().upper()
    # longest class first: "M6E" must not be read as an unknown "M..."
    for cls in sorted(_FUTURE_TICKS, key=len, reverse=True):
        if sym.startswith(cls):
            return _FUTURE_TICKS[cls]
    return _DEFAULT_TICK


def snap(price: float, tick: float, *, toward: str = "nearest") -> float:
    """``price`` on the grid of ``tick``. ``toward`` is "nearest", "down" or
    "up"; a price already on the grid is returned as it is, whatever the
    direction (the tiny epsilon absorbs binary noise such as 1.1 / 0.0001).
    Never below one tick."""
    if tick <= 0:
        raise ValueError("tick must be positive")
    steps = price / tick
    if toward == "down":
        n = math.floor(steps + 1e-9)
    elif toward == "up":
        n = math.ceil(steps - 1e-9)
    elif toward == "nearest":
        n = math.floor(steps + 0.5)
    else:
        raise ValueError(f"toward must be nearest, down or up, not {toward!r}")
    return round(max(n, 1) * tick, 6)
