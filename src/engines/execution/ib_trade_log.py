"""Read IB's own words off an ib_insync ``Trade`` (#320 / #376).

ib_insync appends a ``TradeLogEntry`` per status change and per error
(``errorCode`` set, message like ``Error 201, reqId 7: Order rejected -
reason: ...``). ``orderStatus.whyHeld`` carries IB's hint for a held order.
Both are what the blotter needs to say WHY an order waits or died; the
handlers used to store the bare status string. Pure over duck-typed trades.
"""
from __future__ import annotations

from typing import Any


def ib_reason_from_trade(trade: Any) -> str | None:
    """Latest error message in ``trade.log`` (with ``errorCode`` or starting
    with 'Error'), capped at 300 chars; None when the log holds no error."""
    entries = list(getattr(trade, "log", None) or [])
    for entry in reversed(entries):
        code = getattr(entry, "errorCode", 0) or 0
        msg = str(getattr(entry, "message", "") or "").strip()
        if code or msg.lower().startswith("error"):
            return msg[:300] or f"IB error {code}"
    return None


def ib_why_held(trade: Any) -> str | None:
    """``orderStatus.whyHeld`` trimmed to 60 chars, or None."""
    wh = getattr(getattr(trade, "orderStatus", None), "whyHeld", None)
    wh = str(wh or "").strip()
    return wh[:60] or None
