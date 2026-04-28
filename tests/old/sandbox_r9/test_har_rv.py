"""Tests for core.vol.har_rv — HAR(d/w/m) fit and forward projection."""
from __future__ import annotations

import numpy as np


def _synthetic_closes(n: int = 260, sigma_daily: float = 0.005, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(0.0, sigma_daily, n)
    return 1.17 * np.exp(np.cumsum(log_returns))


def test_fit_returns_none_when_series_too_short() -> None:
    from core.vol.har_rv import fit_har_rv

    assert fit_har_rv([1.17, 1.18, 1.16]) is None


def test_fit_converges_on_stationary_series() -> None:
    from core.vol.har_rv import fit_har_rv

    coef = fit_har_rv(_synthetic_closes())
    assert coef is not None
    # Stationarity condition for HAR : sum of lag coefs < 1.
    assert coef.beta_d + coef.beta_w + coef.beta_m < 1.05  # small slack for noise


def test_projection_returns_sigma_close_to_input_scale() -> None:
    from core.vol.har_rv import _daily_rv_percent_from_closes, fit_har_rv, project_horizon

    # Input σ_daily = 0.005 → σ_annual ≈ 0.005 × √252 × 100 ≈ 7.9 %.
    closes = _synthetic_closes(sigma_daily=0.005, n=300)
    coef = fit_har_rv(closes)
    assert coef is not None
    rv = _daily_rv_percent_from_closes(closes)
    sigma_1m = project_horizon(coef, rv, horizon_days=22)
    # Input σ_daily=0.5% ann ≈ 7.9%. HAR's |r| proxy biases low by
    # √(2/π) ≈ 0.8, so the fit typically lands around 4-10% depending
    # on the seed. Loose band for noise tolerance.
    assert 3.0 <= sigma_1m <= 15.0


def test_fit_and_project_emits_one_row_per_tenor() -> None:
    from core.vol.har_rv import fit_and_project_har

    closes = _synthetic_closes(n=260)
    out = fit_and_project_har(
        closes, tenor_days={"1M": 30, "3M": 90, "6M": 180},
    )
    assert set(out.keys()) == {"1M", "3M", "6M"}
    for node in out.values():
        assert "sigma_har_pct" in node
        assert 0.1 < node["sigma_har_pct"] < 30.0  # sanity range


def test_fit_and_project_empty_on_too_short_series() -> None:
    from core.vol.har_rv import fit_and_project_har

    assert fit_and_project_har(np.array([1.17, 1.18]), {"1M": 30}) == {}
