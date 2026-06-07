"""Unit tests for Step 1 regime engine helpers (pure functions, no DB)."""
from __future__ import annotations

import pytest

from core.vol.regime_engine import (
    compute_regime_snapshot,
    compute_rolling_zscore,
    gate_decision,
)


def test_zscore_returns_none_when_insufficient_history():
    assert compute_rolling_zscore(7.5, []) is None
    assert compute_rolling_zscore(7.5, [7.0, 7.1]) is None
    # Below MIN_OBS_ZSCORE=30 → still None
    assert compute_rolling_zscore(7.5, [7.0] * 29) is None


# === detect_regime tests (spec §10) — alignés sur l'heuristique v1 ====
def test_regime_label_calm_normal_features():
    """Vol bas, vol_of_vol bas → calm."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_level_pct=6.0, vol_of_vol_pct=0.10, term_slope_pct=0.20) == "calm"


def test_regime_label_pre_event_high_vov():
    """Vol_of_vol modérément élevé → pre_event (cf. STEP1 §10)."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_level_pct=8.0, vol_of_vol_pct=0.5, term_slope_pct=-0.5) == "pre_event"


def test_regime_label_stressed_high_vol_level():
    """Vol_level > 10 → stressed."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_level_pct=12.0, vol_of_vol_pct=0.3, term_slope_pct=0.5) == "stressed"


def test_regime_label_stressed_extreme_vov():
    """Vol_of_vol > 1.0 → stressed (extreme jumpiness)."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_level_pct=8.0, vol_of_vol_pct=1.5, term_slope_pct=0.0) == "stressed"


def test_regime_label_pre_event_threshold_boundary():
    """vov = 0.4 inclusif → calm ; vov = 0.41 → pre_event."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_of_vol_pct=0.40) == "calm"
    assert detect_regime(vol_of_vol_pct=0.41) == "pre_event"


# === Reviewer-suggested invariant tests (verrouille l'alignement code/intent) =
def test_regime_label_pre_event_at_vov_threshold():
    """vol_of_vol = 0.45 (entre seuils 0.4 et 1.0) → pre_event."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_level_pct=6.0, vol_of_vol_pct=0.45, term_slope_pct=0.2) == "pre_event"


def test_regime_label_calm_below_vov_threshold():
    """vol_of_vol = 0.35 (< seuil 0.4) → calm peu importe term_slope."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_level_pct=6.0, vol_of_vol_pct=0.35, term_slope_pct=0.2) == "calm"
    # term_slope élevé n'override pas (différent de v0)
    assert detect_regime(vol_level_pct=6.0, vol_of_vol_pct=0.35, term_slope_pct=2.5) == "calm"


def test_regime_label_stressed_overrides_pre_event():
    """vol_of_vol > 1.0 → stressed, pas pre_event (la plus restrictive gagne)."""
    from core.vol.vrp import detect_regime
    assert detect_regime(vol_level_pct=6.0, vol_of_vol_pct=1.5, term_slope_pct=0.2) == "stressed"
    # Cas limite : vov = 1.01 → stressed (pas pre_event)
    assert detect_regime(vol_level_pct=6.0, vol_of_vol_pct=1.01) == "stressed"


def test_zscore_basic():
    # Need ≥ MIN_OBS_ZSCORE = 30 obs ; build a 30-elt history.
    hist = [6.0, 6.5, 7.0, 7.5, 8.0, 6.0, 6.5, 7.0] * 4
    z = compute_rolling_zscore(8.0, hist[:30])
    assert z is not None
    assert z > 0  # 8.0 above mean


def test_compute_regime_snapshot_calm():
    surface = {
        "1M": {"atm": {"iv": 0.06}}, "3M": {"atm": {"iv": 0.062}},
        "6M": {"atm": {"iv": 0.064}}, "_rv_full_pct": 5.5,
    }
    out = compute_regime_snapshot(
        surface=surface, iv_3m_history_pct=[],
        feature_history_rows=[], next_event=None,
        vrp_lookup={("calm", "3M"): 0.8}, now_utc_iso="2026-04-30T10:00:00Z",
    )
    assert out["payload"]["label"] == "calm"
    assert out["payload"]["event_dampener"] is False
    assert out["payload"]["features"]["vol_level"]["value"] == 6.2
    assert out["snapshot_row"]["label"] == "calm"


def test_compute_regime_snapshot_event_dampener_within_5_days():
    surface = {"3M": {"atm": {"iv": 0.06}}}
    out = compute_regime_snapshot(
        surface=surface, iv_3m_history_pct=[],
        feature_history_rows=[],
        next_event={
            "event_type": "ECB", "scheduled_at_iso": "2026-05-02T12:00:00Z",
            "days_remaining": 2.5,
        },
        vrp_lookup={}, now_utc_iso="2026-04-30T00:00:00Z",
    )
    assert out["payload"]["event_dampener"] is True


def test_compute_regime_snapshot_no_dampener_beyond_5_days():
    surface = {"3M": {"atm": {"iv": 0.06}}}
    out = compute_regime_snapshot(
        surface=surface, iv_3m_history_pct=[], feature_history_rows=[],
        next_event={
            "event_type": "ECB", "scheduled_at_iso": "2026-05-30T12:00:00Z",
            "days_remaining": 30.0,
        },
        vrp_lookup={}, now_utc_iso="2026-04-30T00:00:00Z",
    )
    assert out["payload"]["event_dampener"] is False


@pytest.mark.parametrize("label,expected_auth,expected_mult", [
    ("calm", True, 1.0),
    ("stressed", True, 0.7),
    ("pre_event", False, 0.0),
])
def test_gate_decision_stable_history(label, expected_auth, expected_mult):
    d = gate_decision(label, event_dampener=False, history_labels=[label, label, label])
    assert d.authorized is expected_auth
    assert d.size_mult == expected_mult


def test_gate_decision_unstable_blocks():
    d = gate_decision("calm", False, ["calm", "stressed", "calm"])
    assert d.authorized is False
    assert d.reason == "regime_unstable"


def test_gate_decision_short_history_blocks():
    d = gate_decision("calm", False, ["calm"])
    assert d.authorized is False
    assert d.reason == "regime_unstable"


def test_gate_decision_event_dampener_overrides_calm():
    d = gate_decision("calm", event_dampener=True, history_labels=["calm"]*3)
    assert d.authorized is True
    assert d.reason == "event_dampener_active"
    assert d.size_mult == 0.5
