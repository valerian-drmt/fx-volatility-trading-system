"""Unit tests for core.positions.closing.build_closing_legs."""
from __future__ import annotations

from datetime import date

import pytest

from core.positions.closing import EntryLegSnapshot, build_closing_legs


def _entry(side, qty_filled, leg_idx=0):
    return EntryLegSnapshot(
        leg_idx=leg_idx, contract_type="call", contract_strike=1.0850,
        contract_expiry=date(2026, 8, 4), contract_symbol="EUR",
        contract_exchange="CME", contract_currency="USD",
        side=side, qty_filled=qty_filled,
        preview_iv_pct=7.0, preview_price=0.005,
    )


def test_build_closing_legs_inverts_side():
    closing = build_closing_legs([
        _entry("BUY", 5, 0), _entry("SELL", 3, 1),
    ])
    assert [c.side for c in closing] == ["SELL", "BUY"]
    assert [c.qty for c in closing] == [5, 3]


def test_build_closing_skips_unfilled_legs():
    closing = build_closing_legs([
        _entry("BUY", 5, 0), _entry("SELL", 0, 1), _entry("BUY", 2, 2),
    ])
    assert [c.leg_idx for c in closing] == [0, 2]


def test_build_closing_raises_when_nothing_filled():
    with pytest.raises(ValueError, match="qty_filled"):
        build_closing_legs([_entry("BUY", 0, 0), _entry("SELL", 0, 1)])


def test_build_closing_rejects_unknown_side():
    bad = EntryLegSnapshot(
        leg_idx=0, contract_type="call", contract_strike=1.0,
        contract_expiry=date(2026, 8, 4), contract_symbol="EUR",
        contract_exchange="CME", contract_currency="USD",
        side="HOLD", qty_filled=1, preview_iv_pct=None, preview_price=None,
    )
    with pytest.raises(ValueError, match="side"):
        build_closing_legs([bad])
