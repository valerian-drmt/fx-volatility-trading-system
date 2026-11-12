"""Migration 067 (#468): linking the closes that predate ``closes_order_id``.

The SQL of the migration runs in CI's Alembic job on Postgres; the linking
rule is pure and is tested here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[3] / "src/persistence/migrations/versions/067_book_repair.py"
_spec = importlib.util.spec_from_file_location("mig_067", _PATH)
assert _spec is not None and _spec.loader is not None
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

PUT = "EUUZ6 P1075"


def _e(i: int, side: str, filled: int, closed: int = 0, symbol: str = PUT) -> dict:
    return {"id": i, "symbol": symbol, "side": side, "filled": filled, "closed": closed}


def _c(i: int, side: str, filled: int, symbol: str = PUT) -> dict:
    return {"id": i, "symbol": symbol, "side": side, "filled": filled}


def test_a_close_is_linked_to_the_oldest_entry_it_fits() -> None:
    # prod shape: order 75 BUY 100, closed later by a SELL 100 with no link
    assert mig.link_closes([_e(75, "BUY", 100)], [_c(106, "SELL", 100)]) == {106: 75}


def test_first_in_first_out_across_several_entries() -> None:
    links = mig.link_closes(
        [_e(1, "BUY", 10), _e(2, "BUY", 10)],
        [_c(3, "SELL", 10), _c(4, "SELL", 10)],
    )
    assert links == {3: 1, 4: 2}


def test_quantity_already_closed_by_a_linked_order_is_not_available() -> None:
    assert mig.link_closes([_e(1, "BUY", 10, closed=10), _e(2, "BUY", 10)], [_c(3, "SELL", 10)]) == {3: 2}
    assert mig.link_closes([_e(1, "BUY", 10, closed=4)], [_c(3, "SELL", 10)]) == {}, "never over-closes a leg"


def test_only_an_older_entry_on_the_same_contract_and_the_opposite_side() -> None:
    assert mig.link_closes([_e(5, "BUY", 10)], [_c(3, "SELL", 10)]) == {}, "the entry came after the close"
    assert mig.link_closes([_e(1, "SELL", 10)], [_c(3, "SELL", 10)]) == {}, "same side is not a close"
    assert mig.link_closes([_e(1, "BUY", 10, symbol="EUUZ6 C1210")], [_c(3, "SELL", 10)]) == {}
    assert mig.link_closes([_e(1, "BUY", 10)], [_c(3, "SELL", 0)]) == {}, "an unfilled close closes nothing"


def test_revision_chain() -> None:
    assert (mig.revision, mig.down_revision) == ("067_book_repair", "066_instrument")
    assert len(mig.revision) <= 32
