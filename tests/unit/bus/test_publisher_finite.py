"""No NaN / Infinity on the bus (#316, audit 03#12): the publisher turns a
non-finite float into ``null`` at any depth and refuses to emit a frame that
``JSON.parse`` would reject."""
from __future__ import annotations

import json
import math
from decimal import Decimal

import numpy as np

from bus.publisher import _dumps, _finite


def test_non_finite_floats_become_null_at_any_depth() -> None:
    payload = {
        "greeks": {"delta": float("nan"), "vega": 12.5, "nested": [1.0, float("inf"), {"x": -float("inf")}]},
        "np": np.float64("nan"),
        "ok": 3,
        "dec": Decimal("NaN"),
    }
    out = json.loads(_dumps(payload))
    assert out == {"greeks": {"delta": None, "vega": 12.5, "nested": [1.0, None, {"x": None}]}, "np": None, "ok": 3, "dec": None}


def test_a_frame_never_carries_the_nan_token() -> None:
    s = _dumps({"a": math.nan, "b": [math.inf]})
    assert "NaN" not in s and "Infinity" not in s
    assert _finite(1.25) == 1.25 and _finite("NaN") == "NaN", "strings are data, not numbers"
