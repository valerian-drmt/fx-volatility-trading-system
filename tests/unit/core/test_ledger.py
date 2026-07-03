"""Position ledger — average-cost fold of the fill event log (core.ledger).

The audit-grade P&L core, so it's tested hard: long/short round-trips, average
cost on adds, partial closes, a flip through zero, commissions, and unrealized
mark-to-market for both signs.
"""
from __future__ import annotations

import pytest

from core.ledger import ContractLedger, LedgerFill, fold_fills, unrealized_pnl

M = 125_000.0  # a realistic CME EUR-FOP multiplier


def _f(side: str, qty: float, price: float, commission: float = 0.0, mult: float = M) -> LedgerFill:
    return LedgerFill(contract="EUUV6 C1130", side=side, qty=qty, price=price,
                      commission=commission, multiplier=mult)


def _one(fills: list[LedgerFill]) -> ContractLedger:
    return fold_fills(fills)["EUUV6 C1130"]


def test_long_round_trip_realizes_pnl_and_goes_flat():
    led = _one([_f("BUY", 10, 0.02), _f("SELL", 10, 0.03)])
    assert led.net_qty == 0
    assert led.avg_cost == 0.0
    assert led.realized_pnl == pytest.approx((0.03 - 0.02) * 10 * M)  # 12_500


def test_short_round_trip_realizes_pnl():
    # sell to open at 0.03, buy back at 0.01 → made the premium difference
    led = _one([_f("SELL", 5, 0.03), _f("BUY", 5, 0.01)])
    assert led.net_qty == 0
    assert led.realized_pnl == pytest.approx((0.03 - 0.01) * 5 * M)  # 12_500


def test_average_cost_on_adds():
    led = _one([_f("BUY", 10, 0.02), _f("BUY", 10, 0.04)])
    assert led.net_qty == 20
    assert led.avg_cost == pytest.approx(0.03)
    assert led.realized_pnl == 0.0


def test_partial_close_keeps_avg_cost_on_remainder():
    led = _one([_f("BUY", 10, 0.02), _f("SELL", 4, 0.03)])
    assert led.net_qty == 6
    assert led.avg_cost == pytest.approx(0.02)          # unchanged on a reduce
    assert led.realized_pnl == pytest.approx((0.03 - 0.02) * 4 * M)


def test_flip_through_zero_opens_new_position_at_fill_price():
    # long 5 @ 0.02, then SELL 8 @ 0.03 → close 5 (realise), open short 3 @ 0.03
    led = _one([_f("BUY", 5, 0.02), _f("SELL", 8, 0.03)])
    assert led.net_qty == -3
    assert led.avg_cost == pytest.approx(0.03)
    assert led.realized_pnl == pytest.approx((0.03 - 0.02) * 5 * M)


def test_commissions_reduce_realized_pnl():
    led = _one([_f("BUY", 10, 0.02, commission=7.0), _f("SELL", 10, 0.03, commission=7.0)])
    assert led.commission == pytest.approx(14.0)
    assert led.realized_pnl == pytest.approx((0.03 - 0.02) * 10 * M - 14.0)


def test_unrealized_pnl_both_signs():
    long_led = _one([_f("BUY", 10, 0.02)])
    assert unrealized_pnl(long_led, 0.03) == pytest.approx((0.03 - 0.02) * 10 * M)
    short_led = _one([_f("SELL", 10, 0.03)])
    assert unrealized_pnl(short_led, 0.01) == pytest.approx((0.03 - 0.01) * 10 * M)  # short gains as mark falls
    assert unrealized_pnl(long_led, None) is None      # open but no mark
    flat = _one([_f("BUY", 1, 0.02), _f("SELL", 1, 0.02)])
    assert unrealized_pnl(flat, None) == 0.0           # flat → 0 regardless


def test_fills_are_folded_per_contract():
    fills = [
        LedgerFill("A", "BUY", 1, 0.02, 0.0, M),
        LedgerFill("B", "SELL", 2, 0.05, 0.0, M),
        LedgerFill("A", "BUY", 1, 0.04, 0.0, M),
    ]
    out = fold_fills(fills)
    assert out["A"].net_qty == 2 and out["A"].avg_cost == pytest.approx(0.03)
    assert out["B"].net_qty == -2
