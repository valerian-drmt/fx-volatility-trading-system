"""Tests for ``core.vol.pchip_smile`` — delta-pillar smile interpolation.

Resurrected from ``tests/old/test_core_vol.py`` (git 14175622~1), extended
with one case for the SVI-fallback source added since (kwargs are
defaulted, so the historical assertions run untouched).
"""
from __future__ import annotations

import pytest

from core.vol.pchip_smile import DELTA_LABELS, interpolate_delta_pillars

pytestmark = pytest.mark.unit


def test_pchip_interpolates_atm_exactly_when_present():
    # Observations span 0.1 → 0.9 delta with a symmetric smile.
    obs = [
        (0.10, 0.085, 1.12),
        (0.25, 0.078, 1.10),
        (0.50, 0.072, 1.08),
        (0.75, 0.079, 1.06),
        (0.90, 0.086, 1.04),
    ]
    out = interpolate_delta_pillars(obs)
    # PCHIP passes through the observations exactly.
    assert out["atm"].iv == pytest.approx(0.072, abs=1e-9)
    assert out["atm"].strike == pytest.approx(1.08, abs=1e-9)
    assert out["25dc"].iv == pytest.approx(0.078, abs=1e-9)
    assert out["25dp"].iv == pytest.approx(0.079, abs=1e-9)


def test_pchip_returns_none_outside_observed_range():
    obs = [(0.25, 0.078, 1.10), (0.50, 0.072, 1.08), (0.75, 0.079, 1.06)]
    out = interpolate_delta_pillars(obs)
    # 10∆ call / put are outside [0.25, 0.75] → None.
    assert out["10dc"].iv is None
    assert out["10dc"].strike is None
    assert out["10dp"].iv is None


def test_pchip_bails_out_on_fewer_than_three_points():
    out = interpolate_delta_pillars([(0.25, 0.078, 1.10), (0.50, 0.072, 1.08)])
    assert all(p.iv is None and p.strike is None for p in out.values())


def test_delta_labels_are_exhaustive():
    assert set(DELTA_LABELS) == {"atm", "25dc", "25dp", "10dc", "10dp"}


# ── new coverage (2026-07 remediation): SVI fallback source ───────────


def test_pchip_uses_svi_fallback_outside_range_and_stamps_source():
    obs = [(0.25, 0.078, 1.10), (0.50, 0.072, 1.08), (0.75, 0.079, 1.06)]

    def fallback(d: float) -> tuple[float, float]:
        return (0.088, 1.12)

    out = interpolate_delta_pillars(
        obs, fallback=fallback, max_extrapolation_distance=0.20,
    )
    # 10∆ call (0.10) is 0.15 outside the observed support — within the
    # widened extrapolation distance, so the fallback is consulted.
    assert out["10dc"].iv == pytest.approx(0.088)
    assert out["10dc"].strike == pytest.approx(1.12)
    assert out["10dc"].source == "svi_fallback"
    # In-range pillars stay PCHIP-sourced.
    assert out["atm"].source == "pchip"
