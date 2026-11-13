"""The position line set (#461): one list of lines for every panel."""
from __future__ import annotations

import pytest

from core.positions.line_set import (
    BOOK_ONLY,
    BROKER_ONLY,
    CASH,
    MATCHED,
    QTY_MISMATCH,
    BookLot,
    BrokerHolding,
    build_lines,
    unmatched,
)

pytestmark = pytest.mark.unit

PUT, CALL, FUT, SPOT = "EUUZ6 P1075", "EUUZ6 C1210", "6EH7", "EUR.USD"


def _by_id(lines):
    return {ln.line_id: ln for ln in lines}


def test_matched_book_and_broker_give_one_line_per_lot() -> None:
    lines = build_lines(
        [BookLot(CALL, 60, 76, 100), BookLot(FUT, 88, 110, -7)],
        [BrokerHolding(CALL, 100, 1), BrokerHolding(FUT, -7, 2)],
    )
    assert [(ln.line_id, ln.status) for ln in lines] == [(f"{FUT}|T88", MATCHED), (f"{CALL}|T60", MATCHED)]
    assert all(ln.share == 1.0 for ln in lines) and unmatched(lines) == []


def test_prod_2026_11_12_trade_60_put_is_a_flagged_line_everywhere() -> None:
    """The book still held the put the broker had closed: it showed on one
    panel only. It is now one line, flagged, for all three."""
    lines = _by_id(build_lines(
        [BookLot(PUT, 60, 75, 100), BookLot(CALL, 60, 76, 100)],
        [BrokerHolding(CALL, 100, 1)],
    ))
    assert lines[f"{PUT}|T60"].status == BOOK_ONLY and lines[f"{PUT}|T60"].position_id is None
    assert lines[f"{PUT}|T60"].qty == 100 and lines[f"{PUT}|T60"].share == 0.0
    assert lines[f"{CALL}|T60"].status == MATCHED


def test_spot_is_cash_and_an_unexplained_contract_is_broker_only() -> None:
    lines = _by_id(build_lines(
        [], [BrokerHolding(SPOT, -22_533, 54, is_cash=True), BrokerHolding("EUUG7 P1120", 10, 9)],
    ))
    assert lines[f"{SPOT}|-"].status == CASH and lines[f"{SPOT}|-"].trade_id is None
    assert lines["EUUG7 P1120|-"].status == BROKER_ONLY
    assert [ln.line_id for ln in unmatched(lines.values())] == ["EUUG7 P1120|-"]


def test_two_trades_on_one_contract_split_the_holding_oldest_first() -> None:
    lines = _by_id(build_lines(
        [BookLot(CALL, 79, 1, 10), BookLot(CALL, 90, 2, 30)],
        [BrokerHolding(CALL, 40, 5)],
    ))
    a, b = lines[f"{CALL}|T79"], lines[f"{CALL}|T90"]
    assert (a.status, a.broker_qty, a.share) == (MATCHED, 10, 0.25)
    assert (b.status, b.broker_qty, b.share) == (MATCHED, 30, 0.75)
    assert a.position_id == b.position_id == 5


def test_broker_holds_less_or_more_than_the_book() -> None:
    less = _by_id(build_lines([BookLot(CALL, 79, 1, 10), BookLot(CALL, 90, 2, 30)], [BrokerHolding(CALL, 25, 5)]))
    assert less[f"{CALL}|T79"].status == MATCHED
    assert less[f"{CALL}|T90"].status == QTY_MISMATCH and less[f"{CALL}|T90"].broker_qty == 15
    more = _by_id(build_lines([BookLot(CALL, 79, 1, 10)], [BrokerHolding(CALL, 25, 5)]))
    assert more[f"{CALL}|T79"].status == MATCHED
    assert more[f"{CALL}|-"].status == BROKER_ONLY and more[f"{CALL}|-"].broker_qty == 15


def test_opposite_direction_is_never_matched() -> None:
    lines = _by_id(build_lines([BookLot(FUT, 88, 1, 7)], [BrokerHolding(FUT, -7, 3)]))
    assert lines[f"{FUT}|T88"].status == BOOK_ONLY
    assert lines[f"{FUT}|-"].status == BROKER_ONLY and lines[f"{FUT}|-"].broker_qty == -7


def test_legs_of_one_trade_on_one_contract_net_into_one_line() -> None:
    # an entry and its unwind inside the same trade
    lines = build_lines([BookLot(CALL, 79, 1, 10), BookLot(CALL, 79, 2, -10)], [])
    assert lines == []
    kept = build_lines([BookLot(CALL, 79, 1, 10), BookLot(CALL, 79, 2, -4)], [BrokerHolding(CALL, 6, 5)])
    assert [(ln.book_qty, ln.status, ln.order_ids) for ln in kept] == [(6, MATCHED, (1, 2))]


def test_the_set_is_deterministic() -> None:
    lots = [BookLot(CALL, 90, 2, 30), BookLot(PUT, 60, 75, 100), BookLot(CALL, 79, 1, 10)]
    held = [BrokerHolding(CALL, 40, 5)]
    assert [ln.line_id for ln in build_lines(lots, held)] == [ln.line_id for ln in build_lines(lots[::-1], held)]


def test_opposite_trades_on_one_contract_cancel_and_only_the_residual_is_a_line() -> None:
    """The broker nets a contract: a long trade and a short trade on it hold
    nothing between them. Prod: 14 offsetting July trades on 6EZ6."""
    lots = [BookLot("6EZ6", 8, 9, 57), BookLot("6EZ6", 33, 38, -10), BookLot("6EZ6", 63, 80, -100),
            BookLot("6EZ6", 70, 89, 40)]
    lines = build_lines(lots, [])
    assert [(ln.line_id, ln.book_qty, ln.status) for ln in lines] == [("6EZ6|T63", -13, BOOK_ONLY)]
    # fully offsetting trades leave no line at all
    assert build_lines([BookLot("6EZ6", 1, 1, 5), BookLot("6EZ6", 2, 2, -5)], []) == []
    # when the lots add up to what the broker holds, the book is right: every
    # trade keeps its own lot, long or short
    held = build_lines(lots, [BrokerHolding("6EZ6", -13, 7)])
    assert [(ln.line_id, ln.book_qty, ln.status) for ln in held] == [
        ("6EZ6|T8", 57, MATCHED), ("6EZ6|T33", -10, MATCHED), ("6EZ6|T63", -100, MATCHED), ("6EZ6|T70", 40, MATCHED),
    ]


def test_prod_2026_11_13_each_package_keeps_its_own_hedge() -> None:
    """Five trades on 6EH7 (four of them the hedge of an option package):
    +30, +1, -37, +22, +6, and the broker holds 22. Netting the lots first
    showed two lines (16 and 6) and left three packages with no hedge line."""
    lots = [BookLot("6EH7", 98, 120, 30), BookLot("6EH7", 100, 124, 1), BookLot("6EH7", 99, 125, -1),
            BookLot("6EH7", 99, 126, -36), BookLot("6EH7", 101, 127, 22), BookLot("6EH7", 102, 129, 6)]
    lines = build_lines(lots, [BrokerHolding("6EH7", 22, 57)])
    assert [(ln.trade_id, ln.qty, ln.status) for ln in lines] == [
        (98, 30, MATCHED), (99, -37, MATCHED), (100, 1, MATCHED), (101, 22, MATCHED), (102, 6, MATCHED),
    ]
    assert sum(ln.share for ln in lines) == pytest.approx(1.0), "the lines carry the whole holding, no more"
    assert next(ln for ln in lines if ln.trade_id == 99).share < 0, "a short lot carries the opposite greeks"
    # the book and the broker disagree by one future: the disagreement shows on its own
    off = build_lines(lots, [BrokerHolding("6EH7", 21, 57)])
    assert [ln.status for ln in off if ln.status != MATCHED] != []

