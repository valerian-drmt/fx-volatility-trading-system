"""Offline coverage for the migration 032 backfill logic.

The migration calls ``core.products.product_label_from_symbol`` on each
row's (structure / structure_type) pair to populate ``product_label``.
We don't spin up a database here — we replay the exact mapping the
migration applies and assert the deterministic outcome for a
representative fixture of rows lifted from the production data shapes.

If a future change to the helper changes any of these mappings, this
test fails and the migration would silently produce different labels in
prod, which is the situation we want to catch.
"""
from __future__ import annotations

import pytest

from core.products import product_label_from_symbol

# Fixture mirrors the four target-table shapes :
#   position / position_metric_history → backfill from ``structure`` (IB symbol)
#   trade_structure / trade_preview    → backfill from ``structure_type``
_POSITION_LIKE_ROWS: list[tuple[str, str]] = [
    # (ib_symbol, expected_label)
    ("EUUQ6 C1130", "Vanilla Call"),
    ("EUUQ6 P1130", "Vanilla Put"),
    ("EUUX6 C1185", "Vanilla Call"),
    ("EUUN6 P1170", "Vanilla Put"),
    ("6EM6",        "Future - 6E"),
    ("6EU6",        "Future - 6E"),
    ("M6EM6",       "Future - M6E"),
    ("M6EU6",       "Future - M6E"),
]

_TRADE_LIKE_ROWS: list[tuple[str, str]] = [
    # (structure_type, expected_label) — no symbol available on these tables.
    ("vanilla_call",        "Vanilla Call"),
    ("short_vanilla_call",  "Vanilla Call"),
    ("vanilla_put",         "Vanilla Put"),
    ("short_vanilla_put",   "Vanilla Put"),
    ("straddle_atm",        "Straddle"),
    ("short_straddle_atm",  "Straddle"),
    ("long_strangle_25d",   "Strangle"),
    ("short_strangle",      "Strangle"),
    ("long_butterfly_25d",  "Butterfly"),
    ("short_butterfly_25d", "Butterfly"),
    ("calendar_long",       "Calendar"),
    ("calendar_short",      "Calendar"),
    # Future without symbol → default to full-size 6E.
    ("future_buy",          "Future - 6E"),
    ("future_sell",         "Future - 6E"),
]


@pytest.mark.parametrize("symbol,expected", _POSITION_LIKE_ROWS)
def test_position_backfill(symbol: str, expected: str) -> None:
    assert product_label_from_symbol(symbol, None) == expected


@pytest.mark.parametrize("structure_type,expected", _TRADE_LIKE_ROWS)
def test_trade_table_backfill(structure_type: str, expected: str) -> None:
    assert product_label_from_symbol(None, structure_type) == expected


def test_backfill_returns_none_for_garbage_structure_type_no_symbol() -> None:
    # The migration leaves the column NULL in this case (correct
    # behaviour — promoted to NOT NULL in a follow-up migration only
    # after every writer is wired).
    assert product_label_from_symbol(None, "no_such_product") is None
