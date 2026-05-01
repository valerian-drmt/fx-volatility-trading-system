"""Tests for the Q-measure branch of _derive_signals.

After the refactor-plan P1 rewiring, _derive_signals prefers
``_fair_q[tenor]`` (= σ_fair^P + VRP) over the raw ``_garch`` fair IV.
Falls back to the legacy _garch path when _fair_q is absent.
"""
from __future__ import annotations

import pytest


def _surface_with_fair_q(atm, fair_p, vrp, regime="calm"):
    surface: dict = {
        tenor: {"atm": {"iv": iv, "strike": 1.17}} for tenor, iv in atm.items()
    }
    surface["_fair_q"] = {
        tenor: {
            "sigma_fair_p_pct": p,
            "vrp_vol_pts": v,
            "sigma_fair_q_pct": p + v,
            "regime": regime,
        }
        for tenor, (p, v) in ((t, (fair_p[t], vrp[t])) for t in atm)
    }
    surface["_garch"] = {t: {"sigma_model_pct": fair_p[t]} for t in atm}
    surface["_rv_full_pct"] = 5.8
    return surface


def test_signal_uses_sigma_fair_q_when_present() -> None:
    from engines.vol.engine import _derive_signals

    # mid 6.0, fair_p 5.0, VRP 0.6 → fair_q 5.6 → ecart +0.4 → FAIR (< 1.0).
    # Without VRP : ecart = 6.0 - 5.0 = +1.0 → at threshold (would be EXPENSIVE).
    sig = _derive_signals(
        _surface_with_fair_q({"1M": 0.060}, {"1M": 5.0}, {"1M": 0.6}),
        "EURUSD",
    )
    assert len(sig) == 1
    assert sig[0]["signal_type"] == "FAIR"
    assert sig[0]["sigma_fair"] == pytest.approx(5.6)   # Q-value returned
    assert sig[0]["sigma_fair_p"] == pytest.approx(5.0) # P-value preserved
    assert sig[0]["vrp_vol_pts"] == pytest.approx(0.6)
    assert sig[0]["ecart"] == pytest.approx(0.4)


def test_signal_cheap_only_if_below_fair_q() -> None:
    from engines.vol.engine import _derive_signals

    # mid 4.2, fair_p 5.0, VRP 0.6 → fair_q 5.6 → ecart -1.4 → CHEAP.
    sig = _derive_signals(
        _surface_with_fair_q({"3M": 0.042}, {"3M": 5.0}, {"3M": 0.6}),
        "EURUSD",
    )
    assert sig[0]["signal_type"] == "CHEAP"
    assert sig[0]["ecart"] == pytest.approx(-1.4)


def test_fallback_to_garch_when_fair_q_missing() -> None:
    from engines.vol.engine import _derive_signals

    # No _fair_q — legacy path treats GARCH as Q. Same behaviour as
    # R9 sandbox pre-P1.
    surface = {
        "1M": {"atm": {"iv": 0.060, "strike": 1.17}},
        "_garch": {"1M": {"sigma_model_pct": 5.8}},
        "_rv_full_pct": 5.5,
    }
    sig = _derive_signals(surface, "EURUSD")
    assert len(sig) == 1
    assert sig[0]["sigma_fair"] == pytest.approx(5.8)
    assert sig[0]["sigma_fair_p"] == pytest.approx(5.8)
    assert sig[0]["vrp_vol_pts"] == pytest.approx(0.0)


def test_threshold_override_tightens_classification() -> None:
    from engines.vol.engine import _derive_signals

    # With default threshold 1.0, ecart = +0.8 → FAIR. With threshold
    # 0.5 it becomes EXPENSIVE.
    surface = _surface_with_fair_q({"1M": 0.065}, {"1M": 5.1}, {"1M": 0.6})
    # sigma_mid 6.5, sigma_fair_q = 5.1+0.6 = 5.7, ecart +0.8.
    assert _derive_signals(surface, "EURUSD")[0]["signal_type"] == "FAIR"
    assert (
        _derive_signals(surface, "EURUSD", threshold_vol_pts=0.5)[0]["signal_type"]
        == "EXPENSIVE"
    )
