"""Unit tests for the cross-sectional IV-surface z (core.vol.surface_z)."""
from __future__ import annotations

from core.vol.surface_z import cross_sectional_z

TENORS = ["1M", "3M"]
DELTAS = ["10dp", "25dp", "atm", "25dc", "10dc"]


def _surface(row_1m: list[float], row_3m: list[float]) -> dict:
    return {
        "1M": {d: {"iv": v} for d, v in zip(DELTAS, row_1m, strict=True)},
        "3M": {d: {"iv": v} for d, v in zip(DELTAS, row_3m, strict=True)},
    }


def test_wings_positive_atm_negative():
    # Smile: wings high, ATM low → wings z > 0, ATM z < 0.
    surf = _surface([0.08, 0.065, 0.055, 0.064, 0.078], [0.082, 0.067, 0.057, 0.066, 0.08])
    z = cross_sectional_z(surf, TENORS, DELTAS)
    assert z["1M"]["10dp"] > 0 and z["1M"]["10dc"] > 0
    assert z["1M"]["atm"] < 0


def test_put_call_skew_asymmetry_visible():
    # 10dp richer than 10dc (put skew) → z(10dp) > z(10dc).
    surf = _surface([0.085, 0.066, 0.055, 0.063, 0.073], [0.085, 0.066, 0.055, 0.063, 0.073])
    z = cross_sectional_z(surf, TENORS, DELTAS)
    assert z["1M"]["10dp"] > z["1M"]["10dc"]


def test_standardized_mean_zero_unit_std():
    surf = _surface([0.08, 0.065, 0.055, 0.064, 0.078], [0.082, 0.067, 0.057, 0.066, 0.08])
    z = cross_sectional_z(surf, TENORS, DELTAS)
    allz = [v for row in z.values() for v in row.values()]
    assert abs(sum(allz) / len(allz)) < 1e-3  # mean ≈ 0 by construction


def test_flat_surface_yields_empty():
    surf = _surface([0.06] * 5, [0.06] * 5)
    assert cross_sectional_z(surf, TENORS, DELTAS) == {}


def test_too_few_cells_yields_empty():
    surf = {"1M": {"atm": {"iv": 0.06}}}  # 1 cell < min_cells
    assert cross_sectional_z(surf, TENORS, DELTAS) == {}
