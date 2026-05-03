"""Unit tests for core.execution.fills."""
from __future__ import annotations

import pytest

from core.execution.fills import FillEvent, apply_fill_idempotent, update_order_aggregates


def test_idempotent_first_time():
    assert apply_fill_idempotent([], "exec_a") is True


def test_idempotent_duplicate_blocked():
    assert apply_fill_idempotent({"exec_a", "exec_b"}, "exec_a") is False


def test_aggregate_no_fills_yields_empty():
    a = update_order_aggregates([], target_qty=10, side="BUY", preview_price=100.0)
    assert a.qty_filled == 0
    assert a.avg_fill_price is None
    assert a.fully_filled is False


def test_aggregate_volume_weighted_avg():
    fills = [
        FillEvent("e1", qty_filled=5, fill_price=178.42, commission_usd=4.0),
        FillEvent("e2", qty_filled=5, fill_price=178.48, commission_usd=4.0),
    ]
    a = update_order_aggregates(fills, target_qty=10, side="BUY", preview_price=178.40)
    assert a.qty_filled == 10
    assert a.avg_fill_price == pytest.approx(178.45)
    assert a.total_commission_usd == pytest.approx(8.0)
    assert a.slippage_per_contract == pytest.approx(0.05)
    assert a.total_slippage_usd == pytest.approx(0.5)
    assert a.fully_filled is True


def test_aggregate_partial_not_fully_filled():
    fills = [FillEvent("e1", qty_filled=3, fill_price=100.0, commission_usd=2.0)]
    a = update_order_aggregates(fills, target_qty=10, side="BUY", preview_price=100.0)
    assert a.qty_filled == 3
    assert a.fully_filled is False


def test_aggregate_overfill_marks_filled():
    """qty_filled > target shouldn't happen but must not break."""
    fills = [FillEvent("e1", qty_filled=11, fill_price=100.0, commission_usd=2.0)]
    a = update_order_aggregates(fills, target_qty=10, side="BUY", preview_price=100.0)
    assert a.fully_filled is True


def test_aggregate_no_preview_price_skips_slippage():
    fills = [FillEvent("e1", qty_filled=5, fill_price=100.0, commission_usd=2.0)]
    a = update_order_aggregates(fills, target_qty=5, side="BUY", preview_price=None)
    assert a.slippage_per_contract is None
    assert a.total_slippage_usd is None


def test_aggregate_sell_side_slippage_signed():
    """SELL side : positive slippage when avg_fill < preview (received less)."""
    fills = [FillEvent("e1", qty_filled=10, fill_price=99.0, commission_usd=2.0)]
    a = update_order_aggregates(fills, target_qty=10, side="SELL", preview_price=100.0)
    assert a.slippage_per_contract == pytest.approx(1.0)
    assert a.total_slippage_usd == pytest.approx(10.0)
