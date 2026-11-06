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


# ── _iv_for (#310): one accessor, None instead of the 8 % constant ─────────────

_SURFACE = {
    "1M": {
        "25dp": {"iv": 0.081, "strike": 1.07},
        "atm":  {"iv": 0.075, "strike": 1.08},
        "25dc": {"iv": 0.078, "strike": 1.09},
    },
    "4M": {  # interpolated tenor as published: strikes are None
        "25dp": {"iv": 0.082, "strike": None, "source": "interp"},
        "atm":  {"iv": 0.077, "strike": None, "source": "interp"},
        "25dc": {"iv": 0.079, "strike": None, "source": "interp"},
    },
}


def _engine():
    from engines.risk.engine import RiskEngine
    return RiskEngine.__new__(RiskEngine)  # pure helpers only, no connect


def test_iv_for_interpolates_listed_pillars():
    assert _engine()._iv_for(_SURFACE, "1M", 1.075) == pytest.approx(0.078)


def test_iv_for_places_strikeless_interp_pillars_from_delta():
    """Audit 02#6: these pillars were dropped (strike None) → FALLBACK_IV 8 %."""
    eng = _engine()
    assert eng._iv_for(_SURFACE, "4M", 1.08) is None                       # no F/T: cannot place
    iv = eng._iv_for(_SURFACE, "4M", 1.08, F=1.08, T=120 / 365)
    assert iv is not None and 0.077 <= iv <= 0.082


def test_iv_for_returns_none_not_fallback_when_tenor_missing():
    from engines.risk.engine import FALLBACK_IV
    eng = _engine()
    assert eng._iv_for(_SURFACE, "9M", 1.08, F=1.08, T=0.75) is None
    # the counted fallback path is explicit and separate
    eng._iv_fallback_legs = 0
    assert eng._iv_or_fallback(_SURFACE, "9M", 1.08, 1.08, 0.75) == FALLBACK_IV
    assert eng._iv_fallback_legs == 1
    assert eng._iv_or_fallback(_SURFACE, "1M", 1.08, 1.08, 0.1) == pytest.approx(0.075)
    assert eng._iv_fallback_legs == 1
