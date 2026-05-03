"""Delta-hedge decision logic. Cf. STEP5 §4.

Pure function : input current delta + threshold + last_hedge_at, output is
a HedgeDecision (skip / hedge with qty + side). Cooldown is enforced when
the caller passes ``last_hedge_at``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HedgeDecision:
    needs_hedge: bool
    hedge_qty: int = 0                  # always positive ; side gives direction
    side: str = ""                      # 'BUY' | 'SELL'
    delta_imbalance: float = 0.0        # the delta we evaluated against
    threshold_used: float = 0.0
    skip_reason: str | None = None      # 'below_threshold' | 'rounded_to_zero' | 'cooldown'
    post_hedge_residual_delta: float = 0.0


def check_delta_hedge_needed(
    *,
    delta_unhedged: float,
    threshold: float = 0.05,
    min_hedge_qty: int = 1,
    last_hedge_at: datetime | None = None,
    now: datetime | None = None,
    cooldown_seconds: float = 300.0,
) -> HedgeDecision:
    """Evaluate whether a delta-hedge order should fire on the current cycle.

    Conventions :
        - delta_unhedged is in fractional units of underlying (1.0 = 1 future contract worth)
        - Trigger when |delta| > threshold AND outside cooldown
        - Hedge qty = round(|delta|) ; if rounds to 0 → skip
        - Hedge side opposite the imbalance

    cooldown_seconds=0 disables cooldown.
    """
    abs_delta = abs(delta_unhedged)

    if abs_delta < threshold:
        return HedgeDecision(
            needs_hedge=False, delta_imbalance=delta_unhedged,
            threshold_used=threshold, skip_reason="below_threshold",
        )

    # Cooldown
    if last_hedge_at is not None and now is not None and cooldown_seconds > 0:
        elapsed = (now - last_hedge_at).total_seconds()
        if elapsed < cooldown_seconds:
            return HedgeDecision(
                needs_hedge=False, delta_imbalance=delta_unhedged,
                threshold_used=threshold, skip_reason="cooldown",
            )

    # Round and skip if zero
    qty_signed = round(delta_unhedged)
    qty_abs = abs(qty_signed)
    if qty_abs < min_hedge_qty:
        return HedgeDecision(
            needs_hedge=False, delta_imbalance=delta_unhedged,
            threshold_used=threshold, skip_reason="rounded_to_zero",
        )

    # Hedge BUYS underlying when delta is negative (need long delta to offset short),
    # SELLS underlying when delta is positive (need short delta).
    # We send opposite-sign futures.
    side = "SELL" if delta_unhedged > 0 else "BUY"
    residual = delta_unhedged + (-qty_signed)
    return HedgeDecision(
        needs_hedge=True, hedge_qty=qty_abs, side=side,
        delta_imbalance=delta_unhedged, threshold_used=threshold,
        post_hedge_residual_delta=residual,
    )
