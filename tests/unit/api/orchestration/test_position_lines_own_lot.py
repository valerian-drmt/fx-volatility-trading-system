"""A line shows ITS lot: its side, its entry price, its entry time.

Prod, 2026-11-13 (times UTC). A put bought with its hedge at 14:16: the
option line said 14:16, the hedge future line said 13:32. The future was
placed and filled at 14:16:40, one second after the option. 13:32 was when
the broker mirror first saw ANY 6EH7 (the hedge of an earlier trade): the
mirror has one "first seen" and one average price per contract, and every
line on that contract was showing them.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from api.orchestration.position_lines import LineSet, _own_lot, _scale, lot_entry
from core.positions.line_set import BookLot, BrokerHolding, build_lines

pytestmark = pytest.mark.unit

T_OLD = datetime(2026, 11, 13, 13, 32, 7, tzinfo=UTC)
T_NEW = datetime(2026, 11, 13, 14, 16, 40, tzinfo=UTC)


def _line_set() -> LineSet:
    lots = [BookLot("6EH7", 98, 120, 30), BookLot("6EH7", 99, 126, -36), BookLot("6EH7", 99, 125, -1),
            BookLot("6EH7", 101, 127, 23), BookLot("6EH7", 102, 129, 6)]
    legs: dict[int, Any] = {
        120: SimpleNamespace(open_qty=30, avg_price=1.11195), 126: SimpleNamespace(open_qty=-36, avg_price=1.1108),
        125: SimpleNamespace(open_qty=-1, avg_price=1.1108), 127: SimpleNamespace(open_qty=23, avg_price=1.11125),
        129: SimpleNamespace(open_qty=6, avg_price=1.1112),
    }
    return LineSet(
        lines=build_lines(lots, [BrokerHolding("6EH7", 22, 57)]), positions={}, orders={}, legs=legs,
        first_fill={120: T_OLD, 126: datetime(2026, 11, 13, 13, 59, 55, tzinfo=UTC), 129: T_NEW},
    )


def test_each_line_has_the_time_and_price_of_its_own_fills() -> None:
    ls = _line_set()
    by_trade = {ln.trade_id: ln for ln in ls.lines}
    assert lot_entry(ls, by_trade[102]) == (pytest.approx(1.1112), T_NEW)
    assert lot_entry(ls, by_trade[98]) == (pytest.approx(1.11195), T_OLD)
    # two orders of one trade on one contract: one line, the first of the two times
    price, when = lot_entry(ls, by_trade[99])
    assert price == pytest.approx(1.1108) and when == datetime(2026, 11, 13, 13, 59, 55, tzinfo=UTC)
    # no execution recorded: unknown, never the mirror's
    assert lot_entry(ls, by_trade[101])[1] is None


def test_the_mirror_row_is_given_the_lots_own_entry_and_pnl() -> None:
    ls = _line_set()
    ln = next(x for x in ls.lines if x.trade_id == 102)
    mirror = {"side": "BUY", "quantity": 22.0, "market_price": 1.1110, "contract_price_entry": 1.11124173,
              "entry_timestamp": "2026-11-13T13:32:10+00:00", "current_pnl_usd": -634.0, "delta_usd": 3_038_000.0,
              "nominal_eur": 2_750_000.0}
    row = _scale(mirror, ln.share)
    _own_lot(row, ls, ln)
    assert row["quantity"] == pytest.approx(6.0) and row["side"] == "BUY"
    assert row["entry_timestamp"] == T_NEW.isoformat() and row["contract_price_entry"] == pytest.approx(1.1112)
    assert row["current_pnl_usd"] == pytest.approx((1.1110 - 1.1112) * 6 * 125_000)


def test_a_short_lot_in_a_net_long_contract_is_shown_short() -> None:
    ls = _line_set()
    ln = next(x for x in ls.lines if x.trade_id == 99)
    mirror = {"side": "BUY", "quantity": 22.0, "market_price": 1.1110, "delta_usd": 3_038_000.0,
              "nominal_eur": 2_750_000.0, "current_pnl_usd": -634.0}
    row = _scale(mirror, ln.share)
    _own_lot(row, ls, ln)
    assert row["side"] == "SELL" and row["quantity"] == pytest.approx(37.0)
    assert row["delta_usd"] == pytest.approx(-3_038_000.0 * 37 / 22) and row["nominal_eur"] > 0
    assert row["current_pnl_usd"] == pytest.approx((1.1110 - 1.1108) * -37 * 125_000)
