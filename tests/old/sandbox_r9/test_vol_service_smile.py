"""Tests for api.orchestration.vol_service._smile_points — schema tolerance.

get_smile reads the full pillar from vol_surfaces.surface_data. Two
shapes exist in the wild : the legacy flat one (iv_ATM_pct + strike_atm
+ per-delta flat keys) and the engine nested one
({atm: {iv, strike}, 25dc: ..., ...}). The yielded SmilePoints must
be identical, modulo iv decimal -> percent conversion on the nested side.
"""
from __future__ import annotations

import pytest


def test_smile_points_from_flat_legacy_shape() -> None:
    from api.orchestration.vol_service import _smile_points

    pillar = {
        "sigma_ATM_pct": 6.5, "strike_atm": 1.17,
        "iv_25dc_pct": 6.8, "strike_25dc": 1.19,
        "iv_25dp_pct": 6.7, "strike_25dp": 1.15,
        "iv_10dc_pct": 7.5, "strike_10dc": 1.22,
        "iv_10dp_pct": 7.3, "strike_10dp": 1.13,
    }
    points = list(_smile_points(pillar))
    assert len(points) == 5
    labels = [p.delta_label for p in points]
    assert labels == ["10P", "25P", "ATM", "25C", "10C"]
    atm = next(p for p in points if p.delta_label == "ATM")
    assert atm.iv_pct == pytest.approx(6.5)
    assert atm.strike == pytest.approx(1.17)


def test_smile_points_from_nested_engine_shape_converts_iv_to_pct() -> None:
    from api.orchestration.vol_service import _smile_points

    pillar = {
        "atm": {"iv": 0.065, "strike": 1.17},
        "25dc": {"iv": 0.068, "strike": 1.19},
        "25dp": {"iv": 0.067, "strike": 1.15},
        "10dc": {"iv": 0.075, "strike": 1.22},
        "10dp": {"iv": 0.073, "strike": 1.13},
    }
    points = list(_smile_points(pillar))
    assert len(points) == 5
    atm = next(p for p in points if p.delta_label == "ATM")
    assert atm.iv_pct == pytest.approx(6.5)   # 0.065 × 100
    assert atm.strike == pytest.approx(1.17)
    wing = next(p for p in points if p.delta_label == "10C")
    assert wing.iv_pct == pytest.approx(7.5)  # 0.075 × 100


def test_smile_points_drops_partial_nested() -> None:
    """Nested pillar with iv=null / strike=null entries must be skipped."""
    from api.orchestration.vol_service import _smile_points

    pillar = {
        "atm": {"iv": 0.065, "strike": 1.17},
        "25dc": {"iv": None, "strike": None},
        "25dp": {"iv": 0.067, "strike": 1.15},
    }
    labels = {p.delta_label for p in _smile_points(pillar)}
    assert labels == {"ATM", "25P"}


def test_smile_points_empty_pillar_yields_nothing() -> None:
    from api.orchestration.vol_service import _smile_points

    assert list(_smile_points({})) == []
    assert list(_smile_points({"dte": 30, "_rv": 0.05})) == []
