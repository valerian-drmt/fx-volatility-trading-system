"""Order state machine for IB status events (#320, audit 04#4 / EX-05 / EX-06).

Pure: an IB ``orderStatus.status`` string plus the row's current state in,
an explicit :class:`Transition` out. The handler used a bare dict lookup:
unknown statuses were dropped silently, ``PendingCancel`` was unmapped,
``cancelled`` / ``rejected`` overwrote a ``filled`` row, and ``Inactive`` was a
terminal reject even though IB also uses it for an order that is merely not
working yet (outside RTH, exchange halted, price-cap hold) and may re-activate.

Our states (``trade_order.state``):
    pending -> submitted -> partially_filled -> filled
                        \\-> cancelled | rejected | expired

IB statuses (ib_insync ``OrderStatus``): PendingSubmit, ApiPending,
PreSubmitted, Submitted, PendingCancel, Cancelled, ApiCancelled, Filled,
Inactive. Anything else is logged as unknown and ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TERMINAL_STATES: frozenset[str] = frozenset({"filled", "cancelled", "rejected", "expired"})

# IB status -> the state it means, before terminal guards and Inactive semantics.
IB_STATUS_TO_STATE: dict[str, str] = {
    "PendingSubmit": "submitted",
    "ApiPending": "submitted",
    "PreSubmitted": "submitted",
    "Submitted": "submitted",
    "PendingCancel": "submitted",   # still working until IB confirms the cancel
    "Filled": "filled",             # confirmed by executions, never by the status alone
    "Cancelled": "cancelled",
    "ApiCancelled": "cancelled",
    "Inactive": "inactive",         # resolved below: held or rejected
}

Kind = Literal["advance", "noop", "ignored_terminal", "unknown", "held", "fill_pending"]


@dataclass(frozen=True)
class Transition:
    new_state: str | None   # None = do not change the row's state
    kind: Kind
    note: str = ""


def next_state(current: str, ib_status: str | None, *, has_error_reason: bool = False) -> Transition:
    """Decide what an IB status does to a row in ``current`` state.

    ``has_error_reason``: the trade log carries an IB error (e.g. 201 order
    rejected, 110 price increment, 404 shares). That is what separates an
    Inactive REJECT from an Inactive HOLD.
    """
    status = (ib_status or "").strip()
    target = IB_STATUS_TO_STATE.get(status)
    if target is None:
        return Transition(None, "unknown", f"unknown IB status {status!r}")
    if current in TERMINAL_STATES:
        # A late echo (Cancelled after Filled, Submitted after Cancelled) never
        # revives or downgrades a terminal row.
        if target == current:
            return Transition(None, "noop", "already terminal")
        return Transition(None, "ignored_terminal", f"{status} on terminal {current}")
    if target == "filled":
        # Fills terminalise the row; the status alone is not trusted (a stale
        # Filled with no executions would mark qty_filled=0 as filled).
        return Transition(None, "fill_pending", "filled status, waiting for executions")
    if target == "inactive":
        if has_error_reason:
            return Transition("rejected", "advance", "Inactive with IB error: rejected")
        return Transition(None, "held", "Inactive without error: held, still working")
    if target == "submitted":
        if current == "pending":
            return Transition("submitted", "advance", status)
        return Transition(None, "noop", status)  # submitted / partially_filled stay
    if target == "cancelled":
        if has_error_reason:
            # IB "cancels" an order it refused (error + Cancelled): that is a reject.
            return Transition("rejected", "advance", "Cancelled with IB error: rejected")
        return Transition("cancelled", "advance", status)
    return Transition(None, "unknown", f"unhandled mapping {status!r} -> {target!r}")
