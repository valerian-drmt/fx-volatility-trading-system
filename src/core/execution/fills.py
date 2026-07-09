"""Fill aggregation — pure helpers, idempotent on ib_execution_id.

The execution-engine receives `execDetailsEvent` callbacks from IB. A fill
event is identified by `ib_execution_id` (unique per partial). Same id may
be re-delivered (reconnect, retry). This module owns the math :

  - apply_fill_idempotent(existing_ids, new_id) → bool : "should I persist this fill?"
  - update_order_aggregates(fills) → OrderAggregate : recompute qty_filled, avg_fill_price, slippage

All inputs/outputs are plain dataclasses. The DB layer wraps them.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.execution.slippage import compute_slippage_per_contract


@dataclass(frozen=True)
class FillEvent:
    ib_execution_id: str
    qty_filled: int
    fill_price: float
    commission_usd: float


@dataclass(frozen=True)
class OrderAggregate:
    qty_filled: int
    avg_fill_price: float | None
    total_commission_usd: float
    slippage_per_contract: float | None
    total_slippage_usd: float | None
    fully_filled: bool


def apply_fill_idempotent(seen_execution_ids: Iterable[str], new_id: str) -> bool:
    """Return True if the fill is new (caller should persist + aggregate).

    Cheap O(1) test — caller passes the set of already-persisted ids.
    """
    return new_id not in set(seen_execution_ids)


def state_from_recorded_fills(
    recorded_filled: int, target_qty: int, current_state: str,
) -> str | None:
    """Repair a stuck order's state from its OWN recorded fills (the book), or
    ``None`` to leave it. This is the reconciliation authority when the netted IB
    mirror can't confirm a fill — e.g. two trades holding opposite sides of the
    same contract net to zero at IB, so the mirror shows nothing even though the
    leg really executed (a 25Δ put bought against another trade's short).

    Book-only, never invents a fill:
      - recorded fills cover the qty, but the state never left non-terminal
        → ``"filled"`` (the '10/10 still submitted' case) ;
      - some recorded but the order is still ``"submitted"`` (never even reached
        ``partially_filled``) → ``"partially_filled"`` ;
      - nothing recorded (``recorded_filled <= 0``) → ``None`` — no evidence, so
        we NEVER fabricate a fill from the netted mirror here.
    """
    if recorded_filled <= 0:
        return None
    if recorded_filled >= target_qty and current_state != "filled":
        return "filled"
    if current_state == "submitted":
        return "partially_filled"
    return None


def update_order_aggregates(
    fills: Iterable[FillEvent],
    *,
    target_qty: int,
    side: str,
    preview_price: float | None,
) -> OrderAggregate:
    """Recompute aggregate fields on an order from its full fill stream.

    qty_filled = Σ fill.qty
    avg_fill_price = Σ (qty × price) / Σ qty   (volume-weighted)
    total_commission_usd = Σ commission
    slippage_per_contract = signed delta vs preview, side-aware
    total_slippage_usd = slippage × qty_filled
    fully_filled = qty_filled ≥ target_qty
    """
    fills_list = list(fills)
    qty_total = sum(f.qty_filled for f in fills_list)

    if qty_total == 0:
        return OrderAggregate(0, None, 0.0, None, None, False)

    notional = sum(f.qty_filled * f.fill_price for f in fills_list)
    avg = notional / qty_total
    commission = sum(f.commission_usd for f in fills_list)

    if preview_price is None or preview_price <= 0:
        slip_per = None
        slip_total = None
    else:
        slip_per = compute_slippage_per_contract(preview_price, avg, side)
        slip_total = slip_per * qty_total

    return OrderAggregate(
        qty_filled=qty_total,
        avg_fill_price=avg,
        total_commission_usd=commission,
        slippage_per_contract=slip_per,
        total_slippage_usd=slip_total,
        fully_filled=qty_total >= target_qty,
    )
