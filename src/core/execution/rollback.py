"""Rollback decision logic for partially-filled or rejected structures.

Pure : given the current state of orders in a structure and the trigger
event, decide which orders to cancel and which need an unwind opposite-side
order. The actual IB calls (cancel / placeOrder) live in execution-engine.

Rules (cf. STEP4 §7.3 initiate_rollback) :
  - any order in {submitted, acknowledged, partially_filled} → cancel
  - any order with qty_filled > 0 (after cancel) → unwind = opposite side, qty=qty_filled
  - any order in {filled, rejected, cancelled} → leave alone
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


def decide_rollback(orders: Iterable[OrderState]) -> RollbackPlan:
    """Build the cancel + unwind plan for a structure that needs to be rolled back."""
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
            unwinds.append(UnwindAction(
                leg_idx=o.leg_idx,
                side=_OPPOSITE[o.side.upper()],
                qty=o.qty_filled,
            ))
    return RollbackPlan(cancels=cancels, unwinds=unwinds)
