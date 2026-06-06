"""Tests for core.vol.svi — raw SVI fit on a single-tenor smile.

Generate synthetic smiles from known SVI params, fit, and check that
the recovered params + evaluated IVs stay close to the ground truth.
"""
from __future__ import annotations

import numpy as np


def _synth(true_a, true_b, true_rho, true_m, true_sigma, F, T, strikes):
    from core.vol.svi import SviParams, svi_iv

    params = SviParams(a=true_a, b=true_b, rho=true_rho, m=true_m, sigma=true_sigma)
    k = np.log(np.asarray(strikes) / F)
    return svi_iv(k, params, T)


def test_recovers_typical_fx_smile_within_tolerance() -> None:
    from core.vol.svi import fit_svi, svi_iv

    F = 1.17
    T = 1 / 12  # 1M
    strikes = [1.12, 1.15, 1.17, 1.19, 1.22]
    # Typical FX params : small b, slight put skew, convex.
    true_ivs = _synth(0.002, 0.03, -0.15, 0.0, 0.08, F, T, strikes)

    fitted = fit_svi(strikes, true_ivs, forward=F, tenor_years=T)
    assert fitted is not None
    k = np.log(np.asarray(strikes) / F)
    reproduced = svi_iv(k, fitted, T)
    # The fit reproduces each input within 10bp — the bounded-LS optimiser
    # converges tight but not exact on 5 points × 5 params.
    np.testing.assert_allclose(reproduced, true_ivs, atol=1e-3)


def test_svi_curve_returns_requested_number_of_points() -> None:
    from core.vol.svi import SviParams, svi_curve

    params = SviParams(a=0.002, b=0.03, rho=-0.15, m=0.0, sigma=0.08)
    curve = svi_curve(1.17, 1 / 12, params, n_points=25)
    assert len(curve) == 25
    assert all(p["strike"] > 0 and p["iv_pct"] > 0 for p in curve)


def test_fit_returns_none_when_fewer_than_three_points() -> None:
    from core.vol.svi import fit_svi

    assert fit_svi([1.17, 1.18], [0.06, 0.065], forward=1.17, tenor_years=1 / 12) is None


def test_fit_filters_nan_and_negative_ivs() -> None:
    from core.vol.svi import fit_svi

    strikes = [1.12, 1.15, 1.17, 1.19, 1.22]
    ivs = [np.nan, 0.07, 0.06, -1.0, 0.075]  # two invalid out of five
    fitted = fit_svi(strikes, ivs, forward=1.17, tenor_years=1 / 12)
    # Three valid points → fit should succeed.
    assert fitted is not None


def test_curve_shape_on_put_skew_has_higher_iv_on_low_strikes() -> None:
    """Negative rho means put wing higher than call wing — sanity check."""
    from core.vol.svi import SviParams, svi_iv

    params = SviParams(a=0.002, b=0.03, rho=-0.3, m=0.0, sigma=0.08)
    k_puts = np.array([-0.05])   # strike below forward
    k_calls = np.array([+0.05])  # strike above forward
    iv_put = svi_iv(k_puts, params, 1 / 12)[0]
    iv_call = svi_iv(k_calls, params, 1 / 12)[0]
    assert iv_put > iv_call
