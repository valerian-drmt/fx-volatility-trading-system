"""Unit tests for core.execution.slippage."""
from __future__ import annotations

import pytest

from core.execution.slippage import compute_limit_price, compute_slippage_per_contract


def test_buy_limit_above_preview():
    assert compute_limit_price(100.0, "BUY", 0.5) == pytest.approx(100.5)


def test_sell_limit_below_preview():
    assert compute_limit_price(100.0, "SELL", 0.5) == pytest.approx(99.5)


def test_zero_tolerance_returns_preview():
    assert compute_limit_price(178.4, "BUY", 0.0) == 178.4
    assert compute_limit_price(178.4, "SELL", 0.0) == 178.4


def test_invalid_side_raises():
    with pytest.raises(ValueError):
        compute_limit_price(100.0, "FOO", 0.5)


def test_negative_preview_price_raises():
    with pytest.raises(ValueError):
        compute_limit_price(-1.0, "BUY", 0.5)


def test_negative_tolerance_raises():
    with pytest.raises(ValueError):
        compute_limit_price(100.0, "BUY", -0.1)


def test_buy_slippage_positive_when_paid_more():
    s = compute_slippage_per_contract(preview_price=178.40, avg_fill_price=178.45, side="BUY")
    assert s == pytest.approx(0.05)


def test_buy_slippage_negative_when_paid_less():
    s = compute_slippage_per_contract(preview_price=178.40, avg_fill_price=178.30, side="BUY")
    assert s == pytest.approx(-0.10)


def test_sell_slippage_positive_when_received_less():
    s = compute_slippage_per_contract(preview_price=100.0, avg_fill_price=99.0, side="SELL")
    assert s == pytest.approx(1.0)


def test_sell_slippage_negative_when_received_more():
    s = compute_slippage_per_contract(preview_price=100.0, avg_fill_price=101.0, side="SELL")
    assert s == pytest.approx(-1.0)


def test_case_insensitive_side():
    assert compute_limit_price(100.0, "buy", 1.0) == pytest.approx(101.0)
    assert compute_slippage_per_contract(100.0, 99.0, "sell") == pytest.approx(1.0)
