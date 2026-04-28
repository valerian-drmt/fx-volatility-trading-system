"""Tests for core.vol.fair_smile — EWMA anchoring + per-param z-score signals."""
from __future__ import annotations


def _history(n: int, a: float = 0.002, b: float = 0.03) -> list[dict[str, float]]:
    return [
        {"a": a + 0.0001 * i, "b": b, "rho": -0.15, "m": 0.0, "sigma": 0.08}
        for i in range(n)
    ]


def test_ewma_biases_toward_recent_snapshots() -> None:
    from core.vol.fair_smile import ewma_params

    history = [
        {"a": 0.001, "b": 0.03, "rho": -0.2, "m": 0.0, "sigma": 0.08},
        {"a": 0.002, "b": 0.03, "rho": -0.2, "m": 0.0, "sigma": 0.08},
        {"a": 0.010, "b": 0.03, "rho": -0.2, "m": 0.0, "sigma": 0.08},  # most recent outlier
    ]
    fair = ewma_params(history, lambda_=0.5)
    assert fair is not None
    # Most recent snapshot has the largest weight → EWMA 'a' closer to 0.01 than to 0.001.
    assert fair.a > 0.004


def test_ewma_returns_none_on_empty_history() -> None:
    from core.vol.fair_smile import ewma_params

    assert ewma_params([]) is None


def test_signals_are_bootstrap_when_history_short() -> None:
    from core.vol.fair_smile import compute_param_signals

    current = {"a": 0.002, "b": 0.03, "rho": -0.15, "m": 0.0, "sigma": 0.08}
    sigs = compute_param_signals(current, history=_history(5), min_history=30)
    assert all(s.bootstrap for s in sigs)
    assert all(s.z == 0.0 for s in sigs)


def test_signal_z_score_triggers_on_deviation() -> None:
    from core.vol.fair_smile import compute_param_signals

    # 40 points of history with 'a' ≈ 0.002 ± 0.0001.
    history = _history(40, a=0.002)
    current = {"a": 0.010, "b": 0.03, "rho": -0.15, "m": 0.0, "sigma": 0.08}
    sigs = compute_param_signals(current, history=history)
    a_sig = next(s for s in sigs if s.param == "a")
    assert a_sig.bootstrap is False
    assert a_sig.z > 2.0  # strongly above 'a' distribution


def test_fair_iv_evaluates_at_atm_matches_ewma_params() -> None:
    from core.vol.fair_smile import FairSmileParams, fair_iv_at

    # Params chosen so that w(0) ≈ iv²·T = 0.06² × 1/12 → iv ≈ 6% for FX.
    params = FairSmileParams(a=0.0001, b=0.003, rho=-0.15, m=0.0, sigma=0.08)
    iv_atm = fair_iv_at(strike=1.17, forward=1.17, tenor_years=1 / 12, fair=params)
    assert 0.04 < iv_atm < 0.15  # reasonable FX IV band


def test_z_score_summary_is_serializable() -> None:
    from core.vol.fair_smile import compute_param_signals, z_score_summary

    sigs = compute_param_signals(
        {"a": 0.002, "b": 0.03, "rho": -0.15, "m": 0.0, "sigma": 0.08},
        history=_history(40),
    )
    summary = z_score_summary(sigs)
    assert set(summary.keys()) == {"a", "b", "rho", "m", "sigma"}
    for entry in summary.values():
        assert {"current", "fair", "std", "z", "bootstrap"} <= entry.keys()
