"""Unit tests for the FX cash market-hours gate."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shared.market_hours import is_fx_market_open, market_gate_active


@pytest.mark.parametrize("dt, expected", [
    # Saturday — fully closed
    (datetime(2026, 5, 9, 0, 0, tzinfo=UTC),  False),
    (datetime(2026, 5, 9, 12, 0, tzinfo=UTC), False),
    (datetime(2026, 5, 9, 23, 0, tzinfo=UTC), False),
    # Sunday — open from 22:00 UTC
    (datetime(2026, 5, 10, 0, 0, tzinfo=UTC),  False),
    (datetime(2026, 5, 10, 21, 59, tzinfo=UTC), False),
    (datetime(2026, 5, 10, 22, 0, tzinfo=UTC), True),
    (datetime(2026, 5, 10, 23, 0, tzinfo=UTC), True),
    # Monday — open
    (datetime(2026, 5, 11, 0, 0, tzinfo=UTC),  True),
    (datetime(2026, 5, 11, 12, 0, tzinfo=UTC), True),
    # Friday — closed from 22:00 UTC
    (datetime(2026, 5, 15, 21, 59, tzinfo=UTC), True),
    (datetime(2026, 5, 15, 22, 0, tzinfo=UTC), False),
    (datetime(2026, 5, 15, 23, 0, tzinfo=UTC), False),
])
def test_is_fx_market_open(dt: datetime, expected: bool):
    assert is_fx_market_open(dt) is expected


def test_market_gate_active_default(monkeypatch):
    monkeypatch.delenv("FORCE_RUN_MARKET_HOURS", raising=False)
    assert market_gate_active() is True


@pytest.mark.parametrize("flag", ["1", "true", "True"])
def test_market_gate_disabled_via_env(monkeypatch, flag: str):
    monkeypatch.setenv("FORCE_RUN_MARKET_HOURS", flag)
    assert market_gate_active() is False
