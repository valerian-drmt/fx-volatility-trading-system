from __future__ import annotations

import pytest

from core.vol.vrp import VRP_DEFAULTS_VOL_PTS, Regime, detect_regime

TENORS = ("1M", "2M", "3M", "4M", "5M", "6M")


@pytest.mark.parametrize(
    ("level", "vov", "expected"),
    [
        (12.0, 0.1, "stressed"),   # sustained high IV level
        (5.0, 1.5, "stressed"),    # extreme jumpiness dominates
        (5.0, 0.6, "pre_event"),   # moderate vol-of-vol, no sustained level
        (5.0, 0.1, "calm"),        # neither condition met
        (None, None, "calm"),      # missing features default to calm
    ],
)
def test_detect_regime_classifies_on_features(
    level: float | None, vov: float | None, expected: Regime
) -> None:
    assert detect_regime(level, vov, term_slope_pct=0.0) == expected


def test_vrp_defaults_cover_three_regimes_and_all_tenors() -> None:
    assert set(VRP_DEFAULTS_VOL_PTS) == {"calm", "stressed", "pre_event"}
    for curve in VRP_DEFAULTS_VOL_PTS.values():
        assert tuple(curve) == TENORS
        assert all(v > 0 for v in curve.values())  # premium always positive
