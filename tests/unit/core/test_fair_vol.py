"""Unit tests for the restored fair-vol math (R11) — RV / HAR-RV / GARCH / VRP.

Recovered from the v1 pipeline (git b45d9a6). Pure functions, no I/O.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.vol.har_rv import fit_and_project_har, fit_har_rv, project_horizon
from core.vol.vrp import VRP_DEFAULTS_VOL_PTS, predict_vrp, q_measure_from_p
from core.vol.yang_zhang import yang_zhang_rv_pct


def _synthetic_closes(n: int = 300, sigma_daily: float = 0.005, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, sigma_daily, n)
    return 1.10 * np.exp(np.cumsum(steps))


def _ohlc_from_closes(closes: np.ndarray) -> pd.DataFrame:
    # Build plausible OHLC bars around the close path.
    o = closes * (1 + 0.0002)
    h = np.maximum(o, closes) * (1 + 0.0003)
    lo = np.minimum(o, closes) * (1 - 0.0003)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": closes})


# ───────────────────────────── Yang-Zhang RV ────────────────────────────────


def test_yang_zhang_positive_annualised_pct():
    df = _ohlc_from_closes(_synthetic_closes(120))
    rv = yang_zhang_rv_pct(df, window=len(df) - 1)
    assert rv is not None
    assert 0.1 < rv < 50.0  # annualised %, sane band


def test_yang_zhang_none_when_too_short():
    df = _ohlc_from_closes(_synthetic_closes(2))
    assert yang_zhang_rv_pct(df, window=2) is None


# ───────────────────────────── HAR-RV (Corsi) ───────────────────────────────


def test_har_fit_is_stationary():
    coef = fit_har_rv(_synthetic_closes(300))
    assert coef is not None
    assert coef.beta_d + coef.beta_w + coef.beta_m < 1.05  # stationary


def test_har_projection_in_sane_band():
    closes = _synthetic_closes(300, sigma_daily=0.005)
    out = fit_and_project_har(closes, tenor_days={"1M": 30, "3M": 90, "6M": 180})
    assert set(out) == {"1M", "3M", "6M"}
    for node in out.values():
        assert "sigma_har_pct" in node
        assert 0.1 < node["sigma_har_pct"] < 30.0


def test_har_short_series_returns_empty():
    assert fit_and_project_har(_synthetic_closes(20), tenor_days={"1M": 30}) == {}
    assert fit_har_rv(_synthetic_closes(20)) is None


def test_har_project_horizon_guards_non_positive():
    coef = fit_har_rv(_synthetic_closes(300))
    assert coef is not None
    rv = np.abs(np.diff(np.log(_synthetic_closes(300)))) * np.sqrt(252) * 100
    assert np.isnan(project_horizon(coef, rv, horizon_days=0))


# ───────────────────────────── VRP P→Q ──────────────────────────────────────


def test_q_measure_is_p_plus_vrp():
    q, vrp = q_measure_from_p(5.8, tenor="1M", regime="calm")
    assert vrp > 0
    assert q == pytest.approx(5.8 + vrp)


def test_predict_vrp_tenor_aware_calm_upward():
    assert predict_vrp("6M", "calm").value_vol_pts >= predict_vrp("1M", "calm").value_vol_pts


def test_predict_vrp_unknown_tenor_falls_back():
    assert predict_vrp("9M", "calm").value_vol_pts == pytest.approx(0.8)


def test_vrp_table_covers_regimes():
    assert set(VRP_DEFAULTS_VOL_PTS) == {"calm", "stressed", "pre_event"}


# ───────────────────────────── GARCH (arch) ─────────────────────────────────


def test_garch_projects_one_row_per_tenor():
    pytest.importorskip("arch")
    from core.vol.garch import fit_and_project_garch

    out = fit_and_project_garch(_synthetic_closes(300), tenor_t={"1M": 1 / 12, "6M": 0.5})
    assert set(out) == {"1M", "6M"}
    for node in out.values():
        assert 0.1 < node["sigma_model_pct"] < 50.0


def test_garch_empty_on_short_series():
    pytest.importorskip("arch")
    from core.vol.garch import fit_and_project_garch

    assert fit_and_project_garch(np.array([1.1, 1.2, 1.3]), tenor_t={"1M": 1 / 12}) == {}
