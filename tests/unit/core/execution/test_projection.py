"""Unit tests for the pure forward projection (core.execution.projection).

Complements the I3/I7 property loops in tests/unit/oms with explicit shape cases
(avg price, filled_qty tie-back to I1, scenarios T3/T5).
"""
from __future__ import annotations

from core.execution.projection import Fill, fold_fills, signed


def test_signed_buy_positive_sell_negative() -> None:
    assert signed("BUY", 5) == 5
    assert signed("SELL", 5) == -5
    assert signed("sell", 3) == -3  # case-insensitive


def test_empty_is_flat() -> None:
    fold = fold_fills([])
    assert fold.open_qty == 0
    assert fold.filled_qty == 0
    assert fold.avg_price is None


def test_avg_price_is_volume_weighted() -> None:
    fold = fold_fills([Fill("BUY", 2, 1.0), Fill("BUY", 8, 2.0)])
    assert fold.open_qty == 10
    assert fold.filled_qty == 10          # ties back to order.qty_filled (I1)
    assert fold.avg_price == (2 * 1.0 + 8 * 2.0) / 10


def test_t5_partial_then_close_reduces_open() -> None:
    # T5: 7 filled on the entry, then a closing SELL 7 → net flat, 14 traded.
    fold = fold_fills([Fill("BUY", 7, 1.5), Fill("SELL", 7, 1.8)])
    assert fold.open_qty == 0
    assert fold.filled_qty == 14


def test_t3_leg_fold_is_self_contained() -> None:
    # T3: two legs sharing a contract are still exact per-leg — the fold of one
    # leg's fills never depends on another's (attribution is by FK, not netting).
    leg_a = fold_fills([Fill("BUY", 3, 1.0)])
    leg_b = fold_fills([Fill("SELL", 5, 1.0)])
    assert leg_a.open_qty == 3
    assert leg_b.open_qty == -5
