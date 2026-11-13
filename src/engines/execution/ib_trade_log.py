"""Read IB's own words off an ib_async ``Trade`` (#320 / #376).

ib_async appends a ``TradeLogEntry`` per status change and per error
(``errorCode`` set, message like ``Error 201, reqId 7: Order rejected -
reason: ...``). ``orderStatus.whyHeld`` carries IB's hint for a held order.
Both are what the blotter needs to say WHY an order waits or died; the
handlers used to store the bare status string. Pure over duck-typed trades.
"""
from __future__ import annotations

from typing import Any

# IB messages that travel on the error channel but are NOTICES: the order is
# accepted and stays live at the broker. ib_async does not know them as
# warnings, so it marks the trade Cancelled on its side.
#   10349  "Order TIF was set to DAY based on order preset"
# Prod, 2026-11-12: every close sent without a TIF got this notice. Each was
# written "rejected" while it was working at IB; the ones that crossed the
# market filled anyway, and two SELL 100 on a worthless call stayed live at
# the broker, invisible to the desk, on a position of 100.
INFORMATIONAL_CODES = frozenset({10349})


def _errors(trade: Any) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for entry in list(getattr(trade, "log", None) or []):
        code = int(getattr(entry, "errorCode", 0) or 0)
        msg = str(getattr(entry, "message", "") or "").strip()
        if code or msg.lower().startswith("error"):
            out.append((code, msg))
    return out


def ib_reason_from_trade(trade: Any) -> str | None:
    """Latest REAL error message in ``trade.log`` (with ``errorCode`` or
    starting with 'Error'), capped at 300 chars; None when the log holds no
    error. Notices (``INFORMATIONAL_CODES``) are not reasons."""
    for code, msg in reversed(_errors(trade)):
        if code in INFORMATIONAL_CODES:
            continue
        return msg[:300] or f"IB error {code}"
    return None


def is_notice_cancel(trade: Any) -> bool:
    """True when the trade reads Cancelled only because a notice arrived on
    the error channel: the order is still live at the broker and its state
    must not change."""
    status = str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")
    if status not in ("Cancelled", "ApiCancelled"):
        return False
    errors = _errors(trade)
    return bool(errors) and all(code in INFORMATIONAL_CODES for code, _ in errors)


def ib_why_held(trade: Any) -> str | None:
    """``orderStatus.whyHeld`` trimmed to 60 chars, or None."""
    wh = getattr(getattr(trade, "orderStatus", None), "whyHeld", None)
    wh = str(wh or "").strip()
    return wh[:60] or None
