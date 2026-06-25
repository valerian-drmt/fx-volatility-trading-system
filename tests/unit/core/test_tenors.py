"""Unit tests for the listed→display tenor interpolation (surface tenor change)."""
from __future__ import annotations

import math

import pytest

from core.vol.tenors import (
    DISPLAY_PILLARS,
    PILLAR_TARGET_DTE,
    TenorAnchor,
    interpolate_pillar,
    nearest_listed_dte,
)


def _anchor(dte: int, atm: float) -> TenorAnchor:
    # flat-ish smile around atm for the test
    return TenorAnchor(dte=dte, iv_by_pillar={
        "10dp": atm + 0.006, "25dp": atm + 0.002, "atm": atm,
        "25dc": atm + 0.001, "10dc": atm + 0.004,
    })


def test_six_canonical_pillars():
    assert DISPLAY_PILLARS == ("1M", "2M", "3M", "6M", "9M", "1Y")
    assert PILLAR_TARGET_DTE["6M"] == 180 and PILLAR_TARGET_DTE["1Y"] == 365


def test_listed_when_anchor_within_tolerance():
    anchors = [_anchor(28, 0.07), _anchor(95, 0.072)]
    iv, source = interpolate_pillar(anchors, target_dte=30)  # 30 vs 28 → within tol
    assert source == "listed"
    assert iv is not None and iv["atm"] == pytest.approx(0.07)


def test_interp_total_variance_linear_in_time():
    # anchors at 150d (5M) and 250d; target 180d (6M) sits between → interp.
    lo, hi = _anchor(150, 0.10), _anchor(250, 0.20)
    iv, source = interpolate_pillar([lo, hi], target_dte=180)
    assert source == "interp"
    # total variance σ²·t : w150=0.01*150=1.5, w250=0.04*250=10 ; frac=(180-150)/100=0.3
    # w180 = 1.5 + (10-1.5)*0.3 = 4.05 ; iv = sqrt(4.05/180)
    assert iv is not None
    assert iv["atm"] == pytest.approx(math.sqrt(4.05 / 180), rel=1e-9)


def test_missing_when_beyond_furthest_anchor():
    anchors = [_anchor(43, 0.07), _anchor(162, 0.072)]  # furthest 162d
    iv, source = interpolate_pillar(anchors, target_dte=365)  # 1Y far past + margin
    assert source == "missing" and iv is None


def test_flat_hold_just_past_furthest_within_margin():
    anchors = [_anchor(150, 0.07), _anchor(340, 0.09)]  # furthest 340d
    iv, source = interpolate_pillar(anchors, target_dte=365)  # 365-340=25 ≤ margin(45)
    assert source == "interp"
    assert iv is not None and iv["atm"] == pytest.approx(0.09)  # held flat


def test_interp_skips_delta_absent_from_a_bracket():
    lo = TenorAnchor(dte=150, iv_by_pillar={"atm": 0.10, "25dc": 0.10})
    hi = TenorAnchor(dte=250, iv_by_pillar={"atm": 0.20})  # no 25dc
    iv, source = interpolate_pillar([lo, hi], target_dte=180)
    assert source == "interp"
    assert iv is not None and "atm" in iv and "25dc" not in iv


def test_empty_anchors_is_missing():
    iv, source = interpolate_pillar([], target_dte=180)
    assert source == "missing" and iv is None


def test_nearest_listed_dte_snaps_by_abs_distance():
    listed = [43, 71, 106, 134, 162, 254]
    assert nearest_listed_dte(180, listed) == 162  # 6M → nearest listed expiry
    assert nearest_listed_dte(270, listed) == 254
    assert nearest_listed_dte(180, []) is None
