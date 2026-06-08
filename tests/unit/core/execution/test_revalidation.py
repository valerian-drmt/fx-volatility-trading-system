"""Unit tests for core.execution.revalidation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.execution.revalidation import revalidate_preview


def _now() -> datetime:
    return datetime(2026, 5, 3, 12, 0, tzinfo=UTC)


def _ok(**overrides):
    base = dict(
        preview_state="valid_for_submit",
        preview_user_action=None,
        preview_expires_at=_now() + timedelta(seconds=60),
        now=_now(),
        armed_z=2.0, current_z=1.9,
        z_threshold_min=1.5,
        surface_age_seconds=30.0, max_iv_age_seconds=120.0,
        current_regime="calm",
    )
    base.update(overrides)
    return revalidate_preview(**base)


def test_passes_when_all_gates_green():
    r = _ok()
    assert r.passed and r.reason is None


def test_blocks_already_actioned():
    r = _ok(preview_user_action="submitted")
    assert not r.passed and r.reason == "preview_already_actioned"


def test_blocks_state_blocked():
    r = _ok(preview_state="blocked")
    assert not r.passed and r.reason == "preview_state_not_valid"


def test_blocks_expired():
    r = _ok(preview_expires_at=_now() - timedelta(seconds=1))
    assert not r.passed and r.reason == "preview_expired"


def test_blocks_signal_too_weak():
    r = _ok(armed_z=2.0, current_z=0.4)
    assert not r.passed and r.reason == "signal_no_longer_actionable"


def test_blocks_signal_flipped():
    r = _ok(armed_z=2.0, current_z=-1.9)
    assert not r.passed and r.reason == "signal_flipped"


def test_skips_signal_gate_in_manual_mode():
    """Manual previews have armed_z=None — gate must not fire."""
    r = _ok(armed_z=None, current_z=None)
    assert r.passed


def test_blocks_stale_surface():
    r = _ok(surface_age_seconds=300.0, max_iv_age_seconds=120.0)
    assert not r.passed and r.reason == "surface_stale"


def test_blocks_pre_event_regime():
    r = _ok(current_regime="pre_event")
    assert not r.passed and r.reason == "regime_pre_event"


def test_skips_regime_when_none():
    r = _ok(current_regime=None)
    assert r.passed


def test_already_actioned_takes_precedence_over_expired():
    """Two failures simultaneously — user_action wins (audit clarity)."""
    r = _ok(
        preview_user_action="submitted",
        preview_expires_at=_now() - timedelta(seconds=1),
    )
    assert r.reason == "preview_already_actioned"
