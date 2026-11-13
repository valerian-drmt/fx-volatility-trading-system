"""Migration 069 (#494): the state a stuck trade gets from its orders."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[3] / "src/persistence/migrations/versions/069_order_rows_cleanup.py"
_spec = importlib.util.spec_from_file_location("mig_069", _PATH)
assert _spec is not None and _spec.loader is not None
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def test_a_trade_gets_the_state_its_orders_give_it() -> None:
    assert mig.final_state(["cancelled"]) == "fully_failed"
    assert mig.final_state(["rejected", "expired"]) == "fully_failed"
    assert mig.final_state(["filled", "filled"]) == "fully_filled"
    assert mig.final_state(["filled", "cancelled"]) == "partial_fail"


def test_a_trade_with_an_order_still_working_is_left_alone() -> None:
    assert mig.final_state(["filled", "submitted"]) is None
    assert mig.final_state(["pending"]) is None
    assert mig.final_state([]) is None


def test_revision_chain() -> None:
    assert (mig.revision, mig.down_revision) == ("069_order_rows_cleanup", "068_event_writeoff")
    assert len(mig.revision) <= 32
