"""Unit tests for core.positions.delta_hedge."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.positions.delta_hedge import check_delta_hedge_needed


def test_below_threshold_no_hedge():
    d = check_delta_hedge_needed(delta_unhedged=0.03, threshold=0.05)
    assert not d.needs_hedge
    assert d.skip_reason == "below_threshold"


def test_above_threshold_buy_when_negative():
    d = check_delta_hedge_needed(delta_unhedged=-1.4, threshold=0.05)
    assert d.needs_hedge
    assert d.side == "BUY"
    assert d.hedge_qty == 1


def test_above_threshold_sell_when_positive():
    d = check_delta_hedge_needed(delta_unhedged=2.3, threshold=0.05)
    assert d.needs_hedge
    assert d.side == "SELL"
    assert d.hedge_qty == 2


def test_residual_after_hedge():
    d = check_delta_hedge_needed(delta_unhedged=2.3, threshold=0.05)
    # rounded to 2 → residual 0.3 long
    assert d.post_hedge_residual_delta == pytest.approx(0.3)


def test_rounded_to_zero_skips():
    """0.07 > threshold but rounds to 0 contract → skip."""
    d = check_delta_hedge_needed(delta_unhedged=0.07, threshold=0.05)
    assert not d.needs_hedge
    assert d.skip_reason == "rounded_to_zero"


def test_cooldown_blocks():
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    last = now - timedelta(minutes=2)
    d = check_delta_hedge_needed(
        delta_unhedged=2.0, threshold=0.05,
        last_hedge_at=last, now=now, cooldown_seconds=300,
    )
    assert not d.needs_hedge
    assert d.skip_reason == "cooldown"


def test_cooldown_expired_allows():
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    last = now - timedelta(minutes=10)
    d = check_delta_hedge_needed(
        delta_unhedged=2.0, threshold=0.05,
        last_hedge_at=last, now=now, cooldown_seconds=300,
    )
    assert d.needs_hedge


def test_cooldown_zero_disabled():
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    d = check_delta_hedge_needed(
        delta_unhedged=2.0, threshold=0.05,
        last_hedge_at=now, now=now, cooldown_seconds=0,
    )
    assert d.needs_hedge


def test_min_hedge_qty_3_skips_qty_2():
    d = check_delta_hedge_needed(delta_unhedged=2.0, threshold=0.05, min_hedge_qty=3)
    assert not d.needs_hedge
    assert d.skip_reason == "rounded_to_zero"
