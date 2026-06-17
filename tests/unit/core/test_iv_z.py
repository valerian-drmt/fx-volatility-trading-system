"""Unit tests for the per-cell IV z-score (core.vol.iv_z)."""
from __future__ import annotations

from core.vol.iv_z import compute_iv_z

TENORS = ["1M", "3M"]
DELTAS = ["25dp", "atm", "25dc"]


def _surf(atm: float, p25: float, c25: float) -> dict:
    # only the 1M pillar varies in these tests ; 3M kept flat.
    return {
        "1M": {"25dp": {"iv": p25}, "atm": {"iv": atm}, "25dc": {"iv": c25}},
        "3M": {"25dp": {"iv": 0.08}, "atm": {"iv": 0.078}, "25dc": {"iv": 0.082}},
    }


def test_z_positive_when_current_above_history():
    hist = [_surf(0.065, 0.068, 0.066) for _ in range(10)]
    # bump the 1M atm of one history point so std > 0
    hist[0] = _surf(0.064, 0.068, 0.066)
    cur = _surf(0.075, 0.068, 0.066)  # atm well above its history
    z = compute_iv_z(hist, cur, TENORS, DELTAS)
    assert z["1M"]["atm"] > 2.0  # rich
    # flat cells (no dispersion) are omitted
    assert "25dp" not in z.get("1M", {})


def test_z_negative_when_current_below_history():
    hist = [_surf(0.07 + i * 0.0005, 0.072, 0.071) for i in range(10)]
    cur = _surf(0.06, 0.072, 0.071)  # atm below history → cheap
    z = compute_iv_z(hist, cur, TENORS, DELTAS)
    assert z["1M"]["atm"] < 0


def test_insufficient_history_omits_cell():
    hist = [_surf(0.065, 0.068, 0.066) for _ in range(3)]  # < min_obs (8)
    z = compute_iv_z(hist, _surf(0.075, 0.068, 0.066), TENORS, DELTAS)
    assert z == {} or "1M" not in z


def test_missing_current_cell_skipped():
    hist = [_surf(0.065 + i * 0.001, 0.068, 0.066) for i in range(10)]
    cur = {"1M": {"atm": {"iv": 0.07}}}  # 25dp/25dc absent in current
    z = compute_iv_z(hist, cur, TENORS, DELTAS)
    assert "atm" in z["1M"]
    assert "25dp" not in z["1M"]
