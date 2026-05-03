"""Unit tests for core.positions.exit_rules."""
from __future__ import annotations

from core.positions.exit_rules import (
    EXIT_RULES,
    CurrentSignal,
    PositionContext,
    PreEventRegimeRule,
    SignalReverseRule,
    StopLossVegaRule,
    TimeBasedRule,
    TimeToExpiryCriticalRule,
    evaluate_all_rules,
    pick_winning_decision,
)


def _ctx(**overrides) -> PositionContext:
    base = dict(
        position_id=1, triggering_pc=1, entry_z_score=2.0,
        entry_vega_usd_per_volpt=847.0,
        dte_at_entry=90, days_remaining=60,
    )
    base.update(overrides)
    return PositionContext(**base)


def _sig(pc: int, z: float, label: str = "CHEAP") -> dict:
    return {pc: CurrentSignal(pc_id=pc, z_score=z, label=label)}


def test_signal_reverse_flip_triggers_exit():
    rule = SignalReverseRule()
    d = rule.evaluate(_ctx(entry_z_score=2.0), 0.0, _sig(1, -1.5), "calm")
    assert d.triggered and d.action == "EXIT"
    assert d.detail["reason_subtype"] == "flipped"


def test_signal_reverse_weak_triggers_exit():
    rule = SignalReverseRule()
    d = rule.evaluate(_ctx(entry_z_score=2.0), 0.0, _sig(1, 0.3), "calm")
    assert d.triggered and d.action == "EXIT"
    assert d.detail["reason_subtype"] == "weakened"


def test_signal_reverse_50pct_weakening_triggers_trim():
    rule = SignalReverseRule()
    d = rule.evaluate(_ctx(entry_z_score=2.0), 0.0, _sig(1, 0.8), "calm")
    assert d.triggered and d.action == "TRIM"
    assert d.detail["weakening_ratio"] == 0.4


def test_signal_reverse_holds_when_strong():
    rule = SignalReverseRule()
    d = rule.evaluate(_ctx(entry_z_score=2.0), 0.0, _sig(1, 1.8), "calm")
    assert not d.triggered


def test_signal_reverse_no_signal_for_pc():
    rule = SignalReverseRule()
    d = rule.evaluate(_ctx(entry_z_score=2.0), 0.0, _sig(2, 1.5), "calm")
    assert not d.triggered  # signal is for pc=2, position is pc=1


def test_time_based_triggers_below_ratio():
    rule = TimeBasedRule()
    d = rule.evaluate(_ctx(dte_at_entry=90, days_remaining=20), 0.0, {}, None)
    assert d.triggered and d.action == "EXIT"


def test_time_based_holds_above_ratio():
    rule = TimeBasedRule()
    d = rule.evaluate(_ctx(dte_at_entry=90, days_remaining=50), 0.0, {}, None)
    assert not d.triggered


def test_stop_loss_vega_triggers():
    rule = StopLossVegaRule(loss_in_vega_units=3.0)
    # vega 847, threshold = -2541
    d = rule.evaluate(_ctx(entry_vega_usd_per_volpt=847.0), -2700.0, {}, None)
    assert d.triggered
    assert d.detail["implied_iv_move_volpts"] < -3.0


def test_stop_loss_vega_holds_above():
    rule = StopLossVegaRule()
    d = rule.evaluate(_ctx(entry_vega_usd_per_volpt=847.0), -1000.0, {}, None)
    assert not d.triggered


def test_ttm_critical_triggers_under_7():
    rule = TimeToExpiryCriticalRule()
    d = rule.evaluate(_ctx(days_remaining=5), 0.0, {}, None)
    assert d.triggered and d.priority == 5


def test_ttm_critical_holds_above_7():
    rule = TimeToExpiryCriticalRule()
    d = rule.evaluate(_ctx(days_remaining=10), 0.0, {}, None)
    assert not d.triggered


def test_pre_event_regime_triggers():
    rule = PreEventRegimeRule()
    d = rule.evaluate(_ctx(), 0.0, {}, "pre_event")
    assert d.triggered and d.priority == 6


def test_pre_event_regime_holds_in_calm():
    rule = PreEventRegimeRule()
    d = rule.evaluate(_ctx(), 0.0, {}, "calm")
    assert not d.triggered


def test_pick_winning_max_priority():
    """Multiple rules trigger → highest priority wins (pre_event > ttm > signal_reverse)."""
    decisions = evaluate_all_rules(
        EXIT_RULES,
        ctx=_ctx(days_remaining=3, entry_z_score=2.0),
        mtm_pnl_gross_usd=0.0,
        current_signals=_sig(1, -2.0),  # flipped
        regime="pre_event",
    )
    winner = pick_winning_decision(decisions)
    assert winner is not None
    assert winner.rule_name == "pre_event_regime"


def test_pick_winning_returns_none_on_no_trigger():
    decisions = evaluate_all_rules(
        EXIT_RULES,
        ctx=_ctx(days_remaining=60, entry_z_score=2.0),
        mtm_pnl_gross_usd=100.0,
        current_signals=_sig(1, 1.9),
        regime="calm",
    )
    assert pick_winning_decision(decisions) is None
