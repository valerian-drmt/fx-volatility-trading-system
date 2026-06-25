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
    to_display_surface,
)


def _raw_cell(iv: float, strike: float) -> dict:
    return {"iv": iv, "strike": strike, "source": "pchip"}


def _raw_row(atm: float) -> dict:
    return {d: _raw_cell(atm + off, 1.1 + off) for d, off in
            (("10dp", 0.006), ("25dp", 0.002), ("atm", 0.0), ("25dc", 0.001), ("10dc", 0.004))}


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


# ── to_display_surface : raw listed-tenor surface → 6 display pillars ──────────

def test_to_display_surface_six_pillars_with_interp_and_source():
    # Engine emitted 1M..5M monthlies + 9M/1Y quarterlies (no 6M listing).
    raw = {
        "1M": _raw_row(0.070), "2M": _raw_row(0.071), "3M": _raw_row(0.072),
        "4M": _raw_row(0.073), "5M": _raw_row(0.074),
        "9M": _raw_row(0.078), "1Y": _raw_row(0.080),
        "_svi": {"1M": {"butterfly_ok": True}},  # meta carried through
    }
    disp = to_display_surface(raw)
    # 1M,2M,3M listed; 6M interpolated (5M↔9M); 9M,1Y listed. 4M/5M dropped.
    assert set(k for k in disp if not k.startswith("_")) == {"1M", "2M", "3M", "6M", "9M", "1Y"}
    assert "4M" not in disp and "5M" not in disp
    assert disp["_svi"] == raw["_svi"]  # meta preserved
    assert disp["1M"]["atm"]["source"] == "listed"
    assert disp["1M"]["atm"]["strike"] is not None  # real cell kept
    assert disp["6M"]["atm"]["source"] == "interp"
    assert disp["6M"]["atm"]["strike"] is None       # no contract → no strike
    # 6M ATM interpolated between 5M (0.074) and 9M (0.078) → strictly inside
    assert 0.074 < disp["6M"]["atm"]["iv"] < 0.078
    # z recomputed over the display grid
    assert "z" in disp["3M"]["atm"]


def test_to_display_surface_omits_pillar_with_no_anchor_bracket():
    raw = {"1M": _raw_row(0.07), "2M": _raw_row(0.071), "3M": _raw_row(0.072)}
    disp = to_display_surface(raw)
    # only short anchors → 6M/9M/1Y have no bracket and fall outside margin → omitted
    assert set(k for k in disp if not k.startswith("_")) == {"1M", "2M", "3M"}


def test_to_display_surface_empty():
    assert to_display_surface({}) == {}
    assert to_display_surface({"_meta": 1}) == {"_meta": 1}
