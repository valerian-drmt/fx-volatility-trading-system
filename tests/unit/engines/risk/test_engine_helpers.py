"""Unit tests for the pure helpers in risk-engine (no DB / no Redis)."""
from __future__ import annotations

import pytest

from engines.risk.engine import _days_to_tenor_bucket


@pytest.mark.parametrize("days, expected", [
    (0,    "1M"),
    (15,   "1M"),
    (30,   "1M"),
    (31,   "2M"),
    (60,   "2M"),
    (61,   "3M"),
    (90,   "3M"),
    (91,   "4M"),
    (120,  "4M"),
    (121,  "5M"),
    (150,  "5M"),
    (151,  "6M"),
    (365,  "6M"),
])
def test_days_to_tenor_bucket(days: int, expected: str):
    assert _days_to_tenor_bucket(days) == expected
