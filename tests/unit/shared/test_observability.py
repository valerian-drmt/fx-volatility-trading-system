"""Unit tests for shared.observability (P0 LGTM)."""
from __future__ import annotations

import pytest

from shared.observability import (
    clear_cycle,
    cycle_duration,
    cycle_id_var,
    cycles_total,
    last_cycle_ts,
    new_cycle,
    observed_cycle,
)


@pytest.fixture(autouse=True)
def _reset_cycle_id():
    """Reset ContextVar between tests so they don't leak cycle_id."""
    yield
    clear_cycle()


def test_new_cycle_returns_unique_hex():
    a = new_cycle()
    b = new_cycle()
    assert a != b
    assert len(a) == 32  # uuid4 hex
    assert all(c in "0123456789abcdef" for c in a)


def test_new_cycle_sets_contextvar():
    cid = new_cycle()
    assert cycle_id_var.get() == cid


def test_clear_cycle_resets_contextvar():
    new_cycle()
    clear_cycle()
    assert cycle_id_var.get() is None


def test_observed_cycle_increments_counters_on_success():
    before_ok = cycles_total.labels(engine="test", status="ok")._value.get()
    with observed_cycle("test"):
        pass
    after_ok = cycles_total.labels(engine="test", status="ok")._value.get()
    assert after_ok == before_ok + 1


def test_observed_cycle_increments_error_on_exception():
    before_err = cycles_total.labels(engine="test", status="error")._value.get()
    with pytest.raises(ValueError, match="boom"), observed_cycle("test"):
        raise ValueError("boom")
    after_err = cycles_total.labels(engine="test", status="error")._value.get()
    assert after_err == before_err + 1


def test_observed_cycle_records_duration():
    before = cycle_duration.labels(engine="test")._sum.get()
    with observed_cycle("test"):
        pass
    after = cycle_duration.labels(engine="test")._sum.get()
    assert after > before


def test_observed_cycle_sets_last_cycle_timestamp():
    with observed_cycle("test"):
        pass
    ts = last_cycle_ts.labels(engine="test")._value.get()
    assert ts > 0  # unix timestamp


def test_observed_cycle_propagates_cycle_id_to_contextvar():
    with observed_cycle("test") as cid:
        assert cycle_id_var.get() == cid
        assert len(cid) == 32
