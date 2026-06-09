"""Exit-rule evaluation. Cf. STEP5 §3.

5 systematic rules, each pure : given a snapshot (position context, current
mtm, current signals, current regime) → ExitDecision. The monitor picks the
winning decision by max priority when several trigger simultaneously.

Rules implemented :
    1. SignalReverseRule        priority 4 (3 for TRIM)  EXIT/TRIM
    2. TimeBasedRule            priority 2               EXIT
    3. StopLossVegaRule         priority 3               EXIT
    4. TimeToExpiryCriticalRule priority 5               EXIT
    5. PreEventRegimeRule       priority 6               EXIT (max)
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PositionContext:
    position_id: int
    triggering_pc: int | None
    entry_z_score: float | None       # signed z-score at arm time
    entry_vega_usd_per_volpt: float
    dte_at_entry: int                  # days to expiry at entry
    days_remaining: int                # current days to expiry


@dataclass(frozen=True)
class CurrentSignal:
    pc_id: int
    z_score: float
    label: str                         # 'CHEAP' | 'FAIR' | 'EXPENSIVE'


@dataclass(frozen=True)
class ExitDecision:
    triggered: bool
    rule_name: str = ""
    action: str = ""                   # 'EXIT' | 'TRIM' | 'ALERT_ONLY'
    priority: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


class _Rule:
    name: str
    base_priority: int

    def evaluate(self, ctx, mtm_pnl_gross_usd, current_signals, regime) -> ExitDecision:
        raise NotImplementedError


class SignalReverseRule(_Rule):
    name = "signal_reverse"
    base_priority = 4

    def __init__(self, weak_threshold: float = 0.5, weakening_50pct_triggers_trim: bool = True):
        self.weak_threshold = weak_threshold
        self.weakening_50pct_triggers_trim = weakening_50pct_triggers_trim

    def evaluate(self, ctx: PositionContext, mtm_pnl_gross_usd: float,
                 current_signals: dict[int, CurrentSignal], regime: str | None) -> ExitDecision:
        if ctx.triggering_pc is None or ctx.entry_z_score is None:
            return ExitDecision(False)
        current = current_signals.get(ctx.triggering_pc)
        if current is None:
            return ExitDecision(False)

        flipped = (ctx.entry_z_score > 0) != (current.z_score > 0)
        too_weak = abs(current.z_score) < self.weak_threshold

        if flipped or too_weak:
            return ExitDecision(
                triggered=True, rule_name=self.name, action="EXIT",
                priority=self.base_priority,
                detail={
                    "entry_z": ctx.entry_z_score, "current_z": current.z_score,
                    "reason_subtype": "flipped" if flipped else "weakened",
                },
            )

        # Trim trigger : signal weakened by ≥ 50% (vs entry)
        if self.weakening_50pct_triggers_trim and abs(ctx.entry_z_score) > 1e-9:
            ratio = abs(current.z_score) / abs(ctx.entry_z_score)
            if ratio < 0.5:
                return ExitDecision(
                    triggered=True, rule_name=self.name, action="TRIM",
                    priority=self.base_priority - 1,
                    detail={"weakening_ratio": ratio, "entry_z": ctx.entry_z_score,
                            "current_z": current.z_score},
                )

        return ExitDecision(False)


class TimeBasedRule(_Rule):
    name = "time_based"
    base_priority = 2

    def __init__(self, time_remaining_ratio_threshold: float = 0.3):
        self.threshold = time_remaining_ratio_threshold

    def evaluate(self, ctx: PositionContext, *_: Any, **__: Any) -> ExitDecision:
        if ctx.dte_at_entry <= 0:
            return ExitDecision(False)
        ratio = ctx.days_remaining / ctx.dte_at_entry
        if ratio < self.threshold:
            return ExitDecision(
                triggered=True, rule_name=self.name, action="EXIT",
                priority=self.base_priority,
                detail={
                    "days_remaining": ctx.days_remaining,
                    "days_at_entry": ctx.dte_at_entry,
                    "ratio": round(ratio, 3),
                },
            )
        return ExitDecision(False)


class StopLossVegaRule(_Rule):
    name = "stop_loss_vega"
    base_priority = 3

    def __init__(self, loss_in_vega_units: float = 3.0):
        self.loss_units = loss_in_vega_units

    def evaluate(self, ctx: PositionContext, mtm_pnl_gross_usd: float,
                 *_: Any, **__: Any) -> ExitDecision:
        vega_abs = abs(ctx.entry_vega_usd_per_volpt)
        if vega_abs <= 0:
            return ExitDecision(False)
        threshold = -self.loss_units * vega_abs
        if mtm_pnl_gross_usd < threshold:
            return ExitDecision(
                triggered=True, rule_name=self.name, action="EXIT",
                priority=self.base_priority,
                detail={
                    "current_pnl_usd": mtm_pnl_gross_usd,
                    "loss_threshold_usd": threshold,
                    "implied_iv_move_volpts": mtm_pnl_gross_usd / ctx.entry_vega_usd_per_volpt
                        if ctx.entry_vega_usd_per_volpt else None,
                },
            )
        return ExitDecision(False)


class TimeToExpiryCriticalRule(_Rule):
    name = "time_to_expiry_critical"
    base_priority = 5

    def __init__(self, min_days_remaining: int = 7):
        self.min_days = min_days_remaining

    def evaluate(self, ctx: PositionContext, *_: Any, **__: Any) -> ExitDecision:
        if ctx.days_remaining < self.min_days:
            return ExitDecision(
                triggered=True, rule_name=self.name, action="EXIT",
                priority=self.base_priority,
                detail={"days_remaining": ctx.days_remaining, "limit": self.min_days},
            )
        return ExitDecision(False)


class PreEventRegimeRule(_Rule):
    name = "pre_event_regime"
    base_priority = 6

    def __init__(self, trigger_regimes: Sequence[str] = ("pre_event",)):
        self.trigger_regimes = tuple(trigger_regimes)

    def evaluate(self, ctx: PositionContext, mtm_pnl_gross_usd: float,
                 current_signals: dict[int, CurrentSignal], regime: str | None) -> ExitDecision:
        if regime in self.trigger_regimes:
            return ExitDecision(
                triggered=True, rule_name=self.name, action="EXIT",
                priority=self.base_priority,
                detail={"regime": regime},
            )
        return ExitDecision(False)


# Default rule registry — order doesn't matter, priority decides winner.
EXIT_RULES: tuple[_Rule, ...] = (
    SignalReverseRule(),
    TimeBasedRule(),
    StopLossVegaRule(),
    TimeToExpiryCriticalRule(),
    PreEventRegimeRule(),
)


def evaluate_all_rules(
    rules: Iterable[_Rule],
    *,
    ctx: PositionContext,
    mtm_pnl_gross_usd: float,
    current_signals: dict[int, CurrentSignal],
    regime: str | None,
) -> list[ExitDecision]:
    out: list[ExitDecision] = []
    for r in rules:
        d = r.evaluate(ctx, mtm_pnl_gross_usd, current_signals, regime)
        if d.triggered:
            out.append(d)
    return out


def pick_winning_decision(decisions: Sequence[ExitDecision]) -> ExitDecision | None:
    """Return the decision with the highest priority. Ties broken by appearance."""
    triggered = [d for d in decisions if d.triggered]
    if not triggered:
        return None
    return max(triggered, key=lambda d: d.priority)
