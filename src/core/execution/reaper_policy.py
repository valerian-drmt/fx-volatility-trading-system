"""Order-liveness policy — the pure decision behind the reaper (invariant I2).

Kept free of I/O so it is a property-testable function: given an order's state,
its age, and whether IB actually holds the matching contract, decide which
terminal state (if any) the order must be driven to. Terminalising by
TIMEOUT AND IB-absence is the absorbing edge that defect D1 lacked — an order
IB never fills and never cancels otherwise sits non-terminal forever (the "91h"
ghost) and its qty keeps blocking new closes.
"""
from __future__ import annotations

# Absorbing states: once here, an order is done (spec §6.1).
TERMINAL_STATES = frozenset({"filled", "rejected", "cancelled", "expired"})
# Non-terminal states an order can be stuck in *after dispatch*. Pre-dispatch
# 'pending' is the dual-write / outbox concern (P3), not the reaper's.
REAPABLE_STATES = frozenset({"submitted", "acknowledged", "partially_filled"})


def decide_reap(
    *,
    state: str,
    age_s: float,
    tau_s: float,
    held_at_ib: bool,
    matches_contract: bool,
) -> str | None:
    """Return the terminal state a stale order must be driven to, or ``None``.

    - not reapable, or younger than tau -> ``None`` (leave it running)
    - stale AND IB holds the matching contract -> ``"filled"`` (a missed fill,
      reconciled prudently — NEVER a phantom, hence the held AND matches guard)
    - stale AND IB does not hold it -> ``"expired"`` (dead order, absorbing edge)
    """
    if state not in REAPABLE_STATES:
        return None
    if age_s <= tau_s:
        return None
    if held_at_ib and matches_contract:
        return "filled"
    return "expired"
