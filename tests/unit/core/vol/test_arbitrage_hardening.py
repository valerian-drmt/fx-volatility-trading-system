"""Static-arbitrage hardening (#311, audit 02#4 / 02#6): g(k) on the quoted
range plus wings, five points for five parameters, the SSVI conditions of
Gatheral-Jacquier, a cross-tenor calendar check, and an inverted term that
is flagged rather than clamped to a 0 % IV."""
from __future__ import annotations

import math

import numpy as np
import pytest

from core.vol.ssvi import fit_ssvi, ssvi_arbitrage_flags
from core.vol.svi import (
    DEFAULT_K_RANGE,
    MIN_POINTS,
    SviParams,
    butterfly_g_min,
    calendar_gap_min,
    calendar_violations,
    svi_iv,
)
from core.vol.tenors import TenorAnchor, interpolate_pillar, term_inverted

pytestmark = pytest.mark.unit


def test_five_points_are_the_floor_and_the_default_grid_covers_the_wings() -> None:
    assert MIN_POINTS == 5
    assert DEFAULT_K_RANGE == (-0.5, 0.5)


def test_g_min_on_the_quoted_range_sees_a_wing_violation_the_old_grid_missed() -> None:
    # A smile that is fine near ATM and goes negative-density far out on the put wing.
    p = SviParams(a=0.0005, b=0.08, rho=-0.95, m=0.0, sigma=0.02)
    near = butterfly_g_min(p, k_min=-0.2, k_max=0.2)
    wide = butterfly_g_min(p, k_obs=np.array([-0.45, -0.3, -0.1, 0.0, 0.1, 0.3]))
    assert wide <= near
    assert wide < 0 or near < 0, "this fit must fail the density check somewhere on the quoted range"
    # the quoted-range grid is padded on both sides
    assert butterfly_g_min(p, k_obs=[0.0]) == pytest.approx(butterfly_g_min(p, k_min=-0.1, k_max=0.1))


def test_calendar_gap_and_violations_across_slices() -> None:
    short = SviParams(a=0.0010, b=0.05, rho=-0.2, m=0.0, sigma=0.1)
    long_ok = SviParams(a=0.0030, b=0.05, rho=-0.2, m=0.0, sigma=0.1)
    long_bad = SviParams(a=0.0002, b=0.05, rho=-0.2, m=0.0, sigma=0.1)   # below the shorter slice
    assert calendar_gap_min(short, long_ok) > 0
    assert calendar_gap_min(short, long_bad) < 0
    out = calendar_violations([("3M", 0.25, long_bad), ("1M", 1 / 12, short), ("6M", 0.5, long_ok)])
    assert [(r["short"], r["long"], r["ok"]) for r in out] == [("1M", "3M", False), ("3M", "6M", True)]
    assert out[0]["gap_min"] < 0


def test_ssvi_flags_follow_gatheral_jacquier() -> None:
    thetas = {1 / 12: 0.06**2 / 12, 0.25: 0.062**2 * 0.25, 0.5: 0.064**2 * 0.5}
    cal, fly = ssvi_arbitrage_flags(eta=1.2, gamma=0.5, rho=-0.2, theta_by_tenor_years=thetas)
    assert cal and fly
    # gamma above 1 breaks the calendar condition (theta*phi decreasing)
    assert ssvi_arbitrage_flags(1.2, 1.2, -0.2, thetas)[0] is False
    # theta decreasing with maturity is a calendar arbitrage whatever the parameters
    bad_term = {1 / 12: 0.09**2 / 12, 0.25: 0.04**2 * 0.25}
    assert ssvi_arbitrage_flags(1.2, 0.5, -0.2, bad_term)[0] is False
    # a huge eta with |rho| near 1 breaks the butterfly bound theta*phi*(1+|rho|) < 4
    assert ssvi_arbitrage_flags(eta=400.0, gamma=0.5, rho=-0.95, theta_by_tenor_years=thetas)[1] is False


def test_fit_ssvi_reports_both_flags() -> None:
    forward = 1.17
    tenors = [1 / 12, 0.25, 0.5]
    atm = {T: 0.06 + 0.002 * i for i, T in enumerate(tenors)}
    obs = []
    from core.vol.ssvi import ssvi_iv

    for T, a in atm.items():
        for k in (-0.05, -0.02, 0.0, 0.02, 0.05):
            obs.append((T, float(forward * math.exp(k)), float(ssvi_iv(np.array([k]), T, a, 1.2, 0.5, -0.2)[0])))
    fitted = fit_ssvi(obs, forward=forward, atm_iv_by_tenor_years=atm)
    assert fitted is not None
    assert fitted["calendar_arb_free"] is True and fitted["butterfly_arb_free"] is True
    cal, fly = ssvi_arbitrage_flags(fitted["eta"], fitted["gamma"], fitted["rho"], {T: a * a * T for T, a in atm.items()})
    assert (fitted["calendar_arb_free"], fitted["butterfly_arb_free"]) == (cal, fly)


def _anchor(dte: int, iv: float) -> TenorAnchor:
    return TenorAnchor(dte=dte, iv_by_pillar={"atm": iv})


def test_inverted_term_is_flagged_and_never_clamped_to_zero() -> None:
    # 10 % at 150 d, 3 % at 250 d: total variance falls with maturity.
    lo, hi = _anchor(150, 0.10), _anchor(250, 0.03)
    assert term_inverted(0.10, 150, 0.03, 250) and not term_inverted(0.10, 150, 0.20, 250)
    iv, source = interpolate_pillar([lo, hi], target_dte=200)   # 50 d from both anchors: not 'listed'
    assert source == "interp_inverted"
    assert iv is not None and 0.03 <= iv["atm"] <= 0.10, "linear in IV between the anchors, not a clamped 0"
    # a steeper inversion used to cross zero on the variance line and return a clamped 0 %:
    # 10 % at 150 d and 1 % at 250 d, read at 200 d
    iv2, source2 = interpolate_pillar([_anchor(150, 0.10), _anchor(250, 0.01)], target_dte=200)
    assert source2 == "interp_inverted" and iv2 is not None and iv2["atm"] > 0.01
    # a healthy term keeps the variance interpolation and the plain source
    iv3, source3 = interpolate_pillar([_anchor(150, 0.10), _anchor(250, 0.20)], target_dte=180)
    assert source3 == "interp" and iv3 is not None and iv3["atm"] == pytest.approx(math.sqrt(4.05 / 180), rel=1e-9)


def test_svi_iv_still_evaluates_on_the_wide_grid() -> None:
    p = SviParams(a=0.001, b=0.05, rho=-0.2, m=0.0, sigma=0.1)
    iv = svi_iv(np.linspace(*DEFAULT_K_RANGE, 11), p, 0.25)
    assert np.all(np.isfinite(iv)) and np.all(iv > 0)
