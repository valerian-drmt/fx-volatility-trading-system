"""Input sanitising and non-convergence (#312, audit 02#8 / 02#9): Yang-Zhang
drops bad rows and uses n-1 periods, GARCH filters closes, honours the
convergence flag and averages variance over [0, T], HAR seeds the projection
from finite values; GMM standardises its features (#314, audit 02#12)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core.vol.garch import fit_and_project_garch
from core.vol.gmm_regime import FeatureScale, deserialize_gmm, fit_gmm, fit_scale, infer_proba, serialize_gmm
from core.vol.har_rv import MONTHLY_LAG, HarCoef, fit_and_project_har, project_horizon
from core.vol.yang_zhang import yang_zhang_rv_pct

pytestmark = pytest.mark.unit


def _ohlc(n: int, seed: int = 7, vol_pct: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 1.08 * np.cumprod(1 + rng.normal(0, vol_pct / 100, size=n))
    opens = closes * (1 + rng.normal(0, vol_pct / 400, size=n))
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, vol_pct / 400, size=n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, vol_pct / 400, size=n)))
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


# ---- Yang-Zhang ------------------------------------------------------------------------------------

def test_yang_zhang_drops_nan_zero_negative_and_inverted_rows() -> None:
    df = _ohlc(60)
    dirty = df.copy()
    dirty.loc[5, "low"] = np.nan
    dirty.loc[9, "open"] = 0.0
    dirty.loc[12, "close"] = -1.0
    dirty.loc[15, ["high", "low"]] = [1.0, 1.2]   # high below low
    rv = yang_zhang_rv_pct(dirty, window=60)
    assert rv is not None and np.isfinite(rv) and rv > 0
    clean = yang_zhang_rv_pct(df.drop(index=[5, 9, 12, 15]).reset_index(drop=True), window=56)
    assert rv == pytest.approx(clean)


def test_yang_zhang_needs_three_valid_rows_and_uses_periods_in_k() -> None:
    df = _ohlc(5)
    df.loc[[0, 1, 2], "close"] = np.nan
    assert yang_zhang_rv_pct(df, window=5) is None
    # n rows -> n-1 periods: recompute with the textbook k on m = n-1
    df = _ohlc(30)
    o, h, lo, c = (np.log(df[col].values) for col in ("open", "high", "low", "close"))
    overnight = o[1:] - c[:-1]
    oc = c[1:] - o[1:]
    rs = (h[1:] - c[1:]) * (h[1:] - o[1:]) + (lo[1:] - c[1:]) * (lo[1:] - o[1:])
    m = len(df) - 1
    k = 0.34 / (1.34 + (m + 1) / (m - 1))
    s2 = np.var(overnight, ddof=1) + k * np.var(oc, ddof=1) + (1 - k) * np.mean(rs)
    assert yang_zhang_rv_pct(df, window=30) == pytest.approx(float(np.sqrt(s2 * 252) * 100))


# ---- GARCH -------------------------------------------------------------------------------------------

def _closes(n: int = 300, seed: int = 123) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 1.08 * np.cumprod(1 + rng.normal(0, 0.008, size=n))


def test_garch_filters_non_finite_and_non_positive_closes() -> None:
    closes = _closes()
    dirty = closes.copy()
    dirty[10] = np.nan
    dirty[20] = 0.0
    dirty[30] = -3.0
    out = fit_and_project_garch(dirty, tenor_t={"1M": 1 / 12, "3M": 0.25})
    assert set(out) == {"1M", "3M"}
    for node in out.values():
        assert np.isfinite(node["sigma_model_pct"]) and node["sigma_model_pct"] > 0
        assert isinstance(node["garch_converged"], bool)
    assert fit_and_project_garch(np.array([np.nan, 1.0, np.inf, 2.0]), tenor_t={"1M": 1 / 12}) == {}
    assert fit_and_project_garch(np.full(50, 1.1), tenor_t={"1M": 1 / 12}) == {}, "a constant series has no variance to fit"


def test_garch_non_convergence_falls_back_to_the_empirical_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.vol.garch as garch_mod

    class _Fit:
        params = {"omega": 0.01, "alpha[1]": 0.05, "beta[1]": 0.90}
        conditional_volatility = np.array([0.5, 0.6, 0.7])
        convergence_flag = 1   # did NOT converge

    class _Model:
        def __init__(self, *a, **k): ...
        def fit(self, disp="off"):
            return _Fit()

    monkeypatch.setattr(garch_mod, "arch_model", lambda *a, **k: _Model())
    rv_map = {"1M": {"RV_pct": 9.0}}
    out = fit_and_project_garch(_closes(), tenor_t={"1M": 1 / 12}, rv_map=rv_map, rv_full=8.0, blend=0.5)
    assert out["1M"]["garch_converged"] is False
    expected_empirical = 8.0 + (9.0 - 8.0) * np.exp(-2.0 * (1 / 12))
    assert out["1M"]["sigma_model_pct"] == pytest.approx(round(float(expected_empirical), 4))


def test_garch_term_structure_is_the_average_variance_over_the_horizon(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.vol.garch as garch_mod

    class _Fit:
        params = {"omega": 0.02, "alpha[1]": 0.05, "beta[1]": 0.90}
        conditional_volatility = np.array([1.5])   # well above the long run: a decaying path
        convergence_flag = 0

    class _Model:
        def __init__(self, *a, **k): ...
        def fit(self, disp="off"):
            return _Fit()

    monkeypatch.setattr(garch_mod, "arch_model", lambda *a, **k: _Model())
    out = fit_and_project_garch(_closes(), tenor_t={"short": 0.02, "long": 2.0}, blend=1.0)
    persistence = 0.95
    kappa = -np.log(persistence)
    var_c = (np.sqrt(1.5**2 * 252) / 100) ** 2
    var_lr = (np.sqrt(0.02 / (1 - persistence) * 252) / 100) ** 2

    def avg(T: float) -> float:
        return float(np.sqrt(var_lr + (var_c - var_lr) * (1 - np.exp(-kappa * T)) / (kappa * T)) * 100)

    def instantaneous(T: float) -> float:
        return float(np.sqrt(var_lr + (var_c - var_lr) * np.exp(-kappa * T)) * 100)

    for label, T in (("short", 0.02), ("long", 2.0)):
        assert out[label]["sigma_model_pct"] == pytest.approx(round(avg(T), 4))
    # the average of a decaying path over [0, T] sits above its endpoint
    assert out["long"]["sigma_model_pct"] > round(instantaneous(2.0), 4)


# ---- HAR -------------------------------------------------------------------------------------------------

def test_har_projection_seeds_from_finite_positive_values_only() -> None:
    coef = HarCoef(beta0=0.0, beta_d=1.0, beta_w=0.0, beta_m=0.0, sigma_residual=0.1)
    series = np.array([50.0] * 40 + [np.nan, 0.0, -1.0] + [10.0] * 10)
    out = project_horizon(coef, series, horizon_days=3)
    assert np.isfinite(out) and out == pytest.approx(10.0)
    assert np.isnan(project_horizon(coef, np.array([np.nan, np.inf]), horizon_days=3))
    assert MONTHLY_LAG == 22


def test_fit_and_project_har_survives_dirty_closes() -> None:
    closes = _closes(400)
    dirty = closes.copy()
    dirty[[3, 77, 150]] = [np.nan, 0.0, -2.0]
    out = fit_and_project_har(dirty, tenor_days={"1M": 30, "3M": 90})
    assert set(out) == {"1M", "3M"}
    assert all(np.isfinite(v["sigma_har_pct"]) and v["sigma_har_pct"] > 0 for v in out.values())


# ---- GMM ---------------------------------------------------------------------------------------------------

def _clusters(n: int = 80, seed: int = 42, scale: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    means = [(5.0, 1.0, 0.5), (9.0, 2.0, -3.0), (16.0, 5.0, 1.0)]
    X = np.vstack([rng.normal(m, 0.8, size=(n, 3)) for m in means])
    return X * np.asarray(scale)


def test_gmm_standardises_features_and_the_scale_round_trips() -> None:
    X = _clusters(scale=(1.0, 0.001, 1000.0))   # wildly different units
    gmm, fit = fit_gmm(X)
    assert gmm is not None and fit is not None and fit.scale is not None
    assert all(s > 0 for s in fit.scale.std)
    stressed = np.array([16.0, 5.0 * 0.001, 1.0 * 1000.0])
    assert infer_proba(gmm, stressed, fit).label == "stressed"
    calm = np.array([5.0, 1.0 * 0.001, 0.5 * 1000.0])
    assert infer_proba(gmm, calm, fit).label == "calm"
    gmm2, fit2 = deserialize_gmm(json.loads(json.dumps(serialize_gmm(gmm, fit))))
    assert fit2.scale == fit.scale
    assert infer_proba(gmm2, stressed, fit2) == infer_proba(gmm, stressed, fit)


def test_gmm_constant_feature_and_nan_rows_are_handled() -> None:
    X = _clusters()
    X[:, 2] = 0.5                       # a constant feature: std 0 must not divide
    X[3] = [np.nan, 1.0, 0.5]           # a NaN row is dropped, not propagated
    gmm, fit = fit_gmm(X)
    assert gmm is not None and fit is not None
    assert fit.scale is not None and fit.scale.std[2] == 1.0 and fit.n_obs == len(X) - 1
    res = infer_proba(gmm, np.array([16.0, 5.0, 0.5]), fit)
    assert res.label == "stressed" and abs(res.p_calm + res.p_stressed + res.p_pre_event - 1.0) < 1e-6
    assert fit_scale(np.array([[1.0, 2.0], [1.0, 4.0]])).std == (1.0, 1.0)


def test_a_model_published_without_a_scale_still_infers_in_raw_units() -> None:
    X = _clusters()
    gmm, fit = fit_gmm(X)
    payload = serialize_gmm(gmm, fit)
    payload.pop("scale")
    gmm2, fit2 = deserialize_gmm(payload)
    assert fit2.scale is None
    assert FeatureScale(mean=(0.0,), std=(1.0,)).apply(np.array([2.0]))[0] == 2.0
    # it predicts (on standardised-space parameters, so labels may differ) without raising
    assert infer_proba(gmm2, np.array([16.0, 5.0, 1.0]), fit2).label in {"calm", "pre_event", "stressed"}
