"""Pure-Python execution helpers (no DB / no IB).

Re-validation, slippage / limit-price math, fill aggregation, rollback
decision logic. Imported by the api ``/trade/submit`` endpoint and by the
future execution-engine fills handler.
"""
from core.execution.fills import (
    OrderAggregate,
    apply_fill_idempotent,
    update_order_aggregates,
)
from core.execution.revalidation import RevalidationResult, revalidate_preview
from core.execution.rollback import RollbackPlan, decide_rollback
from core.execution.slippage import compute_limit_price, compute_slippage_per_contract

__all__ = [
    "OrderAggregate",
    "RevalidationResult",
    "RollbackPlan",
    "apply_fill_idempotent",
    "compute_limit_price",
    "compute_slippage_per_contract",
    "decide_rollback",
    "revalidate_preview",
    "update_order_aggregates",
]
