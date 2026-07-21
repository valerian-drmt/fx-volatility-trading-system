"""Rollback decision logic for partially-filled or rejected structures.

Pure : given the current state of orders in a structure and the trigger
event, decide which orders to cancel and which need an unwind opposite-side
order. The actual IB calls (cancel / placeOrder) live in execution-engine.

Rules (cf. STEP4 §7.3 initiate_rollback) :
  - any order in {submitted, acknowledged, partially_filled} → cancel
  - any order with qty_filled > 0 (after cancel) → unwind = opposite side,
    qty = RESIDUAL fill not already covered by a prior unwind (EXEC-3)
  - any order in {filled, rejected, cancelled} → leave alone

Idempotency (EXEC-3) : prior unwind orders are passed in as ``UnwindState``
rows. Non-failed unwinds (pending / submitted / partially_filled / filled)
count as already covering their quantity — only the residual
``qty_filled - covered`` is re-unwound, so calling rollback twice can never
flip a flattened structure into a net inverse position.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderState:
    leg_idx: int
    state: str           # 'pending'|'submitted'|'acknowledged'|'partially_filled'|'filled'|'rejected'|'cancelled'
    side: str            # 'BUY'|'SELL'
    qty: int
    qty_filled: int


@dataclass(frozen=True)
class UnwindState:
    """State of a prior unwind order (order_role='unwind') for one leg."""

    leg_idx: int
    state: str           # same vocabulary as OrderState.state, plus 'expired'
    qty: int
    qty_filled: int


@dataclass(frozen=True)
class CancelAction:
    leg_idx: int


@dataclass(frozen=True)
class UnwindAction:
    leg_idx: int
    side: str            # opposite of original
    qty: int


@dataclass(frozen=True)
class RollbackPlan:
    cancels: list[CancelAction]
    unwinds: list[UnwindAction]

    def is_noop(self) -> bool:
        return not self.cancels and not self.unwinds


_OPPOSITE = {"BUY": "SELL", "SELL": "BUY"}
_CANCELLABLE_STATES = frozenset({"submitted", "acknowledged", "partially_filled", "pending"})
# Unwind orders in these states cover nothing — their qty must be re-unwound.
_FAILED_UNWIND_STATES = frozenset({"rejected", "cancelled", "expired"})


def decide_rollback(
    orders: Iterable[OrderState],
    existing_unwinds: Iterable[UnwindState] = (),
) -> RollbackPlan:
    """Build the cancel + unwind plan for a structure that needs to be rolled back.

    ``existing_unwinds`` (EXEC-3) : prior unwind orders of the structure. A
    non-failed unwind counts as covering its full ``qty`` (it is either live
    at IB or already filled) ; only the residual per leg is emitted."""
    covered: dict[int, int] = {}
    for u in existing_unwinds:
        if u.state not in _FAILED_UNWIND_STATES:
            covered[u.leg_idx] = covered.get(u.leg_idx, 0) + u.qty

    cancels: list[CancelAction] = []
    unwinds: list[UnwindAction] = []
    for o in orders:
        if o.state in _CANCELLABLE_STATES:
            cancels.append(CancelAction(leg_idx=o.leg_idx))
        # Unwind any partial fill regardless of cancel result — naked exposure
        # must be flattened. If state == 'filled' (fully) we also unwind because
        # the rollback was triggered AFTER a partial leg succeeded — the spec
        # is ambiguous here, but flattening is safer.
        if o.qty_filled > 0 and o.state in ("partially_filled", "filled"):
            residual = o.qty_filled - covered.get(o.leg_idx, 0)
            if residual > 0:
                unwinds.append(UnwindAction(
                    leg_idx=o.leg_idx,
                    side=_OPPOSITE[o.side.upper()],
                    qty=residual,
                ))
    return RollbackPlan(cancels=cancels, unwinds=unwinds)
