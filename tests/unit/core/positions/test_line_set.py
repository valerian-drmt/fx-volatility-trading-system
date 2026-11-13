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
    is_pooled,
    unmatched,
)

pytestmark = pytest.mark.unit

PUT, CALL, FUT, SPOT = "EUUZ6 P1075", "EUUZ6 C1210", "6EH7", "EUR.USD"


def _by_id(lines):
    return {ln.line_id: ln for ln in lines}


def test_matched_book_and_broker_give_one_line_per_lot() -> None:
    lines = build_lines(
        [BookLot(CALL, 60, 76, 100), BookLot(PUT, 88, 110, -7)],
        [BrokerHolding(CALL, 100, 1), BrokerHolding(PUT, -7, 2)],
    )
    assert [(ln.line_id, ln.status) for ln in lines] == [(f"{CALL}|T60", MATCHED), (f"{PUT}|T88", MATCHED)]
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
    lines = _by_id(build_lines([BookLot(CALL, 88, 1, 7)], [BrokerHolding(CALL, -7, 3)]))
    assert lines[f"{CALL}|T88"].status == BOOK_ONLY
    assert lines[f"{CALL}|-"].status == BROKER_ONLY and lines[f"{CALL}|-"].broker_qty == -7


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
    nothing between them. Prod: offsetting trades on one option."""
    lots = [BookLot("EUUG7 C1090", 8, 9, 57), BookLot("EUUG7 C1090", 33, 38, -10), BookLot("EUUG7 C1090", 63, 80, -100),
            BookLot("EUUG7 C1090", 70, 89, 40)]
    lines = build_lines(lots, [])
    assert [(ln.line_id, ln.book_qty, ln.status) for ln in lines] == [("EUUG7 C1090|T63", -13, BOOK_ONLY)]
    # fully offsetting trades leave no line at all
    assert build_lines([BookLot("EUUG7 C1090", 1, 1, 5), BookLot("EUUG7 C1090", 2, 2, -5)], []) == []
    # when the lots add up to what the broker holds, the book is right: every
    # trade keeps its own lot, long or short
    held = build_lines(lots, [BrokerHolding("EUUG7 C1090", -13, 7)])
    assert [(ln.line_id, ln.book_qty, ln.status) for ln in held] == [
        ("EUUG7 C1090|T8", 57, MATCHED), ("EUUG7 C1090|T33", -10, MATCHED), ("EUUG7 C1090|T63", -100, MATCHED), ("EUUG7 C1090|T70", 40, MATCHED),
    ]


def test_trades_on_one_option_each_keep_their_lot_when_the_book_is_right() -> None:
    lots = [BookLot(CALL, 98, 120, 30), BookLot(CALL, 99, 126, -37), BookLot(CALL, 101, 127, 22),
            BookLot(CALL, 102, 129, 7)]
    lines = build_lines(lots, [BrokerHolding(CALL, 22, 57)])
    assert [(ln.trade_id, ln.qty, ln.status) for ln in lines] == [
        (98, 30, MATCHED), (99, -37, MATCHED), (101, 22, MATCHED), (102, 7, MATCHED),
    ]
    assert sum(ln.share for ln in lines) == pytest.approx(1.0), "the lines carry the whole holding, no more"
    assert next(ln for ln in lines if ln.trade_id == 99).share < 0, "a short lot carries the opposite greeks"


def test_a_future_is_one_line_per_contract_whatever_trades_hold_it() -> None:
    """Owner's rule, 2026-11-13. Five trades on 6EH7 (four of them the hedge
    of an option package): +30, +1, -37, +22, +6, and the broker holds 22.
    A trade is its options; the future is one position of the account."""
    lots = [BookLot("6EH7", 98, 120, 30), BookLot("6EH7", 100, 124, 1), BookLot("6EH7", 99, 125, -1),
            BookLot("6EH7", 99, 126, -36), BookLot("6EH7", 101, 127, 22), BookLot("6EH7", 102, 129, 6),
            BookLot(PUT, 102, 128, 22)]
    lines = build_lines(lots, [BrokerHolding("6EH7", 22, 57), BrokerHolding(PUT, 22, 58)])
    assert [(ln.line_id, ln.trade_id, ln.qty, ln.status, ln.share) for ln in lines] == [
        ("6EH7|-", None, 22, MATCHED, 1.0), (f"{PUT}|T102", 102, 22, MATCHED, 1.0),
    ]
    assert is_pooled("6EH7") and is_pooled("M6EZ6") and not is_pooled(PUT) and not is_pooled(SPOT)
    # the book and the broker disagree: still one line, and it says so
    off = build_lines(lots[:6], [BrokerHolding("6EH7", 21, 57)])
    assert [(ln.line_id, ln.book_qty, ln.broker_qty, ln.status) for ln in off] == [("6EH7|-", 22, 21, QTY_MISMATCH)]
    assert [ln.status for ln in build_lines(lots[:6], [])] == [BOOK_ONLY]
    assert [ln.status for ln in build_lines([], [BrokerHolding("6EH7", 3, 57)])] == [BROKER_ONLY]
    # futures that cancel in the book and are not held: nothing to show
    assert build_lines([BookLot("6EH7", 1, 1, 5), BookLot("6EH7", 2, 2, -5)], []) == []
