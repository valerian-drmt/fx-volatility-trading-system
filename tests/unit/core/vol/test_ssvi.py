"""Tests for core.vol.ssvi — surface-level SSVI fit (Gatheral-Jacquier 2014)."""
from __future__ import annotations

import numpy as np
import pytest


def _synthetic_surface(forward: float = 1.17) -> tuple[list[tuple[float, float, float]], dict[float, float]]:
    """Generate observations from known SSVI parameters for a recovery test."""
    from core.vol.ssvi import ssvi_iv

    # Use gamma=0.5, rho=-0.2 so that 2γ=1.0 ≥ 1-ρ²=0.96 (calendar-arb free).
    eta_true, gamma_true, rho_true = 1.2, 0.5, -0.2
    tenors_years = [1 / 12, 3 / 12, 6 / 12]
    atm_by_T = {T: 0.06 + 0.002 * idx for idx, T in enumerate(tenors_years)}
    observations: list[tuple[float, float, float]] = []
    for T, atm_iv in atm_by_T.items():
        for k in (-0.05, -0.02, 0.0, 0.02, 0.05):
            strike = forward * np.exp(k)
            iv = ssvi_iv(np.array([k]), T, atm_iv, eta_true, gamma_true, rho_true)[0]
            observations.append((T, float(strike), float(iv)))
    return observations, atm_by_T


def test_ssvi_recovers_known_parameters() -> None:
    from core.vol.ssvi import fit_ssvi

    obs, atm = _synthetic_surface()
    fitted = fit_ssvi(obs, forward=1.17, atm_iv_by_tenor_years=atm)
    assert fitted is not None
    assert fitted["eta"] == pytest.approx(1.2, abs=0.5)
    assert fitted["gamma"] == pytest.approx(0.5, abs=0.2)
    assert fitted["rho"] == pytest.approx(-0.2, abs=0.3)
    assert fitted["rmse_fit"] < 0.01


def test_ssvi_returns_none_on_insufficient_observations() -> None:
    from core.vol.ssvi import fit_ssvi

    # Fewer than 5 observations.
    obs = [(1 / 12, 1.17, 0.06), (1 / 12, 1.18, 0.065)]
    assert fit_ssvi(obs, forward=1.17, atm_iv_by_tenor_years={1 / 12: 0.06}) is None


def test_ssvi_reports_calendar_arb_free_flag() -> None:
    from core.vol.ssvi import fit_ssvi

    obs, atm = _synthetic_surface()
    fitted = fit_ssvi(obs, forward=1.17, atm_iv_by_tenor_years=atm)
    assert fitted is not None
    # Our bounds enforce gamma ≥ 0.05 ; for modest |rho| this is calendar-free.
    assert fitted["calendar_arb_free"] is True


def test_ssvi_curve_returns_requested_points() -> None:
    from core.vol.ssvi import ssvi_curve_for_tenor

    curve = ssvi_curve_for_tenor(
        forward=1.17, tenor_years=1 / 12, atm_iv=0.06,
        eta=1.2, gamma=0.3, rho=-0.2, n_points=25,
    )
    assert len(curve) == 25
    assert all(p["strike"] > 0 and p["iv_pct"] > 0 for p in curve)
