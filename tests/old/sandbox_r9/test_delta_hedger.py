"""Tests for engines.execution.delta_hedger — hedge decision logic."""
from __future__ import annotations


def test_static_mode_never_hedges_after_entry() -> None:
    from engines.execution.delta_hedger import decide_hedge

    assert decide_hedge(net_delta=2.5, mode="static").should_hedge is False


def test_threshold_mode_hedges_when_exceeded() -> None:
    from engines.execution.delta_hedger import decide_hedge

    d = decide_hedge(net_delta=0.15, mode="threshold", threshold=0.05)
    assert d.should_hedge is True
    assert d.qty == 0   # rounds(-0.15) = 0 ? Actually round(-0.15) = 0 in Python banker's — check real val.
    # Use a larger imbalance to get a deterministic rounded qty.
    d2 = decide_hedge(net_delta=1.5, mode="threshold", threshold=0.05)
    assert d2.should_hedge is True
    assert d2.qty == -2   # hedge opposite direction, round(1.5) = 2 (banker's → 2)


def test_threshold_mode_stays_flat_below_threshold() -> None:
    from engines.execution.delta_hedger import decide_hedge

    d = decide_hedge(net_delta=0.01, mode="threshold", threshold=0.05)
    assert d.should_hedge is False
    assert d.qty == 0


def test_scheduled_mode_waits_for_elapsed_time() -> None:
    from engines.execution.delta_hedger import decide_hedge

    too_soon = decide_hedge(
        net_delta=1.0, mode="scheduled",
        last_hedge_seconds_ago=60.0, rebalance_every_s=900.0,
    )
    assert too_soon.should_hedge is False
    ready = decide_hedge(
        net_delta=1.0, mode="scheduled",
        last_hedge_seconds_ago=1000.0, rebalance_every_s=900.0,
    )
    assert ready.should_hedge is True
    assert ready.qty == -1


def test_split_hedge_pnl_separates_option_and_hedge_components() -> None:
    from engines.execution.delta_hedger import split_hedge_pnl

    pnl = split_hedge_pnl(
        option_mtm_change=1500.0, hedge_qty=-3, spot_change=0.002, fut_multiplier=125000.0,
    )
    assert pnl.options_pnl == 1500.0
    assert pnl.hedge_pnl == -750.0   # -3 × 0.002 × 125000
    assert pnl.total == 750.0
