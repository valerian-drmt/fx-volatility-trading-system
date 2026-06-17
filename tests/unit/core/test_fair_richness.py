"""Unit tests for the per-cell fair-richness colour (core.vol.fair_richness)."""
from __future__ import annotations

from core.vol.fair_richness import build_fair_richness

DELTAS = ["10dp", "25dp", "atm", "25dc", "10dc"]


def _surface(iv_1m: float, iv_3m: float) -> dict:
    return {
        "1M": {"atm": {"iv": iv_1m}},
        "3M": {"atm": {"iv": iv_3m}},
    }


def _fair_q(q_1m: float, q_3m: float) -> dict:
    return {
        "1M": {"sigma_fair_q_pct": q_1m},
        "3M": {"sigma_fair_q_pct": q_3m},
    }


def test_rich_when_iv_above_fair():
    # IV 8.5% vs fair 5.5% → +3.0 vp gap → +2.0 z at scale 1.5.
    z = build_fair_richness(_surface(0.085, 0.06), _fair_q(5.5, 6.0), DELTAS)
    assert z["1M"]["atm"] > 0
    assert z["1M"]["atm"] == 2.0  # (8.5 - 5.5)/1.5
    # broadcast across the row : every delta shares the tenor's richness.
    assert set(z["1M"]) == set(DELTAS)
    assert all(v == z["1M"]["atm"] for v in z["1M"].values())


def test_cheap_when_iv_below_fair():
    z = build_fair_richness(_surface(0.05, 0.055), _fair_q(6.5, 6.0), DELTAS)
    assert z["1M"]["atm"] < 0  # (5.0 - 6.5)/1.5 = -1.0


def test_neutral_when_iv_equals_fair():
    z = build_fair_richness(_surface(0.06, 0.06), _fair_q(6.0, 6.0), DELTAS)
    assert z["1M"]["atm"] == 0.0


def test_tenor_omitted_when_fair_missing():
    z = build_fair_richness(_surface(0.06, 0.06), {"1M": {"sigma_fair_q_pct": 5.0}}, DELTAS)
    assert "1M" in z and "3M" not in z


def test_empty_fair_q_yields_empty():
    assert build_fair_richness(_surface(0.06, 0.06), {}, DELTAS) == {}
