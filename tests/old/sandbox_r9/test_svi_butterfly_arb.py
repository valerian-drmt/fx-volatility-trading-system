"""Tests for core.vol.svi.butterfly_g_min — no-arb density health check."""
from __future__ import annotations


def test_clean_smile_has_positive_g_min() -> None:
    from core.vol.svi import SviParams, butterfly_g_min

    # Mild-skew, convex FX smile — should be butterfly-arbitrage free.
    params = SviParams(a=0.002, b=0.03, rho=-0.15, m=0.0, sigma=0.08)
    assert butterfly_g_min(params) > -1e-3  # small slack for grid discretisation


def test_extreme_skew_triggers_violation() -> None:
    from core.vol.svi import SviParams, butterfly_g_min

    # Extreme rho + tiny sigma → spiky smile, density goes negative.
    params = SviParams(a=0.001, b=0.3, rho=-0.98, m=0.0, sigma=0.005)
    assert butterfly_g_min(params) < 0


def test_fitted_smile_from_realistic_data_is_arb_free() -> None:
    from core.vol.svi import butterfly_g_min, fit_svi

    # 5 pillars typical of a calm EURUSD 1M smile.
    strikes = [1.13, 1.15, 1.17, 1.19, 1.22]
    ivs = [0.0736, 0.0645, 0.0600, 0.0608, 0.0663]
    params = fit_svi(strikes, ivs, forward=1.17, tenor_years=1 / 12)
    assert params is not None
    # A well-fit FX smile at 5 pillars is almost always arbitrage-free.
    assert butterfly_g_min(params) > -1e-3
