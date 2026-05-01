"""Tests for engines.vol.engine._derive_signals — CHEAP / EXPENSIVE / FAIR.

Takes the engine surface (with per-tenor ATM IV and the _garch fair
vol per tenor) and emits one signal dict per tenor, classified against
a 100bp threshold by default.
"""
from __future__ import annotations

import pytest


def _surface(atm_by_tenor, fair_by_tenor, rv=2.87):
    surface: dict = {}
    for tenor, iv in atm_by_tenor.items():
        surface[tenor] = {"atm": {"iv": iv, "strike": 1.17}}
    surface["_garch"] = {t: {"sigma_model_pct": f} for t, f in fair_by_tenor.items()}
    surface["_rv_full_pct"] = rv
    return surface


def test_classifies_fair_when_ecart_within_threshold() -> None:
    from engines.vol.engine import _derive_signals

    # mid=6.00%, fair=5.80% → ecart=+0.20 (<1pt threshold) → FAIR.
    sig = _derive_signals(_surface({"1M": 0.060}, {"1M": 5.80}), "EURUSD")
    assert len(sig) == 1
    assert sig[0]["signal_type"] == "FAIR"
    assert sig[0]["sigma_mid"] == pytest.approx(6.0)
    assert sig[0]["sigma_fair"] == pytest.approx(5.8)
    assert sig[0]["ecart"] == pytest.approx(0.2)
    assert sig[0]["tenor"] == "1M"
    assert sig[0]["dte"] == 30
    assert sig[0]["underlying"] == "EURUSD"


def test_classifies_expensive_when_mid_well_above_fair() -> None:
    from engines.vol.engine import _derive_signals

    # mid=7.50%, fair=5.80% → ecart=+1.70 (>1pt) → EXPENSIVE.
    sig = _derive_signals(_surface({"3M": 0.075}, {"3M": 5.80}), "EURUSD")
    assert sig[0]["signal_type"] == "EXPENSIVE"


def test_classifies_cheap_when_mid_well_below_fair() -> None:
    from engines.vol.engine import _derive_signals

    # mid=4.00%, fair=5.80% → ecart=-1.80 (<-1pt) → CHEAP.
    sig = _derive_signals(_surface({"6M": 0.040}, {"6M": 5.80}), "EURUSD")
    assert sig[0]["signal_type"] == "CHEAP"


def test_multi_tenor_emits_one_row_per_tenor_with_garch_match() -> None:
    from engines.vol.engine import _derive_signals

    surface = _surface(
        {"1M": 0.060, "3M": 0.072, "6M": 0.085},
        {"1M": 5.8, "3M": 6.5, "6M": 7.0},
    )
    sig = _derive_signals(surface, "EURUSD")
    assert {s["tenor"] for s in sig} == {"1M", "3M", "6M"}
    types = {s["tenor"]: s["signal_type"] for s in sig}
    # 1M ecart=+0.2 FAIR ; 3M ecart=+0.7 FAIR ; 6M ecart=+1.5 above 1.0pt threshold → EXPENSIVE.
    assert types == {"1M": "FAIR", "3M": "FAIR", "6M": "EXPENSIVE"}


def test_skips_tenor_without_garch_fair() -> None:
    from engines.vol.engine import _derive_signals

    # 2M has no entry in _garch → skipped.
    surface = _surface({"1M": 0.060, "2M": 0.065}, {"1M": 5.8})
    sig = _derive_signals(surface, "EURUSD")
    assert {s["tenor"] for s in sig} == {"1M"}


def test_skips_underscore_keys_and_non_dict_pillars() -> None:
    from engines.vol.engine import _derive_signals

    surface = {
        "1M": {"atm": {"iv": 0.060, "strike": 1.17}},
        "_rv_full_pct": 2.87,
        "_garch": {"1M": {"sigma_model_pct": 5.8}},
        "weird": "not-a-dict",
    }
    sig = _derive_signals(surface, "EURUSD")
    assert [s["tenor"] for s in sig] == ["1M"]
