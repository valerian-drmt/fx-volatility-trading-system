"""Tests for ``core.vol.garch`` — GARCH(1,1) term-structure projection.

Sanity bands resurrected from ``tests/old/test_core_vol.py`` (git 14175622~1);
the parameter-recovery test on a simulated GARCH(1,1) series is new
(2026-07 remediation, plan 04 item 7).
"""
from __future__ import annotations

import numpy as np
import pytest

from core.vol.garch import fit_and_project_garch

pytestmark = pytest.mark.unit


def test_garch_empty_when_insufficient_data():
    assert fit_and_project_garch(np.array([1.0, 1.01, 1.02]), tenor_t={"1M": 1 / 12}) == {}


def test_garch_returns_positive_sigmas_for_each_tenor():
    rng = np.random.default_rng(seed=123)
    # 200 days of 0.8% daily shocks around 1.08.
    returns = rng.normal(0, 0.008, size=200)
    closes = 1.08 * np.cumprod(1 + returns)

    tenor_t = {"1M": 1 / 12, "3M": 3 / 12, "1Y": 1.0}
    out = fit_and_project_garch(closes, tenor_t=tenor_t)

    assert set(out) == set(tenor_t)
    for label in tenor_t:
        sigma = out[label]["sigma_model_pct"]
        assert 1.0 < sigma < 40.0, f"{label}={sigma}% out of sanity range"


def test_garch_empirical_blend_tends_to_rv_when_blend_is_zero():
    rng = np.random.default_rng(seed=7)
    closes = 1.08 * np.cumprod(1 + rng.normal(0, 0.01, size=150))

    rv_map = {"1M": {"RV_pct": 8.0}}
    out = fit_and_project_garch(
        closes, tenor_t={"1M": 1 / 12}, rv_map=rv_map, rv_full=12.0,
        blend=0.0, emp_kappa=2.0,
    )
    # blend=0 → pure empirical leg : rv_full + (rv_tenor - rv_full) * exp(-kappa*T)
    expected = 12.0 + (8.0 - 12.0) * np.exp(-2.0 / 12)
    assert out["1M"]["sigma_model_pct"] == pytest.approx(expected, abs=1e-3)


# ── new coverage (2026-07 remediation): parameter recovery ────────────


def _simulate_garch_11(
    omega: float, alpha: float, beta: float, n: int, burn: int, seed: int,
) -> np.ndarray:
    """Simulate GARCH(1,1) daily returns in percent."""
    rng = np.random.default_rng(seed)
    var_uncond = omega / (1.0 - alpha - beta)
    var = var_uncond
    r_prev = 0.0
    out = np.empty(n + burn)
    for t in range(n + burn):
        var = omega + alpha * r_prev * r_prev + beta * var
        r_prev = np.sqrt(var) * rng.standard_normal()
        out[t] = r_prev
    return out[burn:]


def test_garch_recovers_long_run_vol_of_simulated_process():
    """``fit_and_project_garch`` on a known DGP: the long-horizon projection
    must land near the theoretical unconditional annualised vol.

    DGP: ω=0.02, α=0.05, β=0.90 (persistence 0.95), returns in %.
    σ_LR = sqrt(ω/(1−α−β) · 252) = sqrt(0.4 · 252) ≈ 10.04 %.
    """
    omega, alpha, beta = 0.02, 0.05, 0.90
    returns_pct = _simulate_garch_11(omega, alpha, beta, n=2000, burn=200, seed=42)
    closes = 1.08 * np.exp(np.cumsum(returns_pct / 100.0))

    tenor_t = {"1M": 1 / 12, "1Y": 1.0, "5Y": 5.0}
    out = fit_and_project_garch(closes, tenor_t=tenor_t, blend=1.0)
    assert set(out) == set(tenor_t)

    sigma_lr_theory = float(np.sqrt(omega / (1.0 - alpha - beta) * 252.0))  # ≈ 10.04
    assert out["5Y"]["sigma_model_pct"] == pytest.approx(sigma_lr_theory, rel=0.15)

    # Mean reversion: var_T = var_lr + (var_c − var_lr)·e^{−κT} is monotone
    # in T (direction depends on the last conditional vol) — assert the
    # term structure between 1M and 5Y is monotone, not its direction.
    s = [out["1M"]["sigma_model_pct"], out["1Y"]["sigma_model_pct"], out["5Y"]["sigma_model_pct"]]
    diffs = np.diff(s)
    assert np.all(diffs >= -1e-9) or np.all(diffs <= 1e-9), f"non-monotone term structure: {s}"
