"""One signal threshold between Arm and Submit (#314, audit 02#13): the
pre-submit check and the revalidation agree on the same z, through the same
constant and the same function."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.execution.revalidation import SIGNAL_ACTIONABLE_FACTOR, revalidate_preview, signal_still_actionable
from core.trade_preview import run_pre_submit_checks

pytestmark = pytest.mark.unit

THRESHOLD = 1.5


def _preview_check(armed_z: float, current_z: float) -> bool:
    checks = run_pre_submit_checks(
        regime={"regime": "calm"},
        armed_z=armed_z, current_z=current_z, threshold_min=THRESHOLD,
        max_loss_usd=100.0, capital_total_usd=1_000_000.0, max_loss_pct=0.02,
        book_total_vega_usd=0.0, structure_vega_usd=100.0, max_book_vega_usd=1e9,
        surface_age_seconds=10.0, max_iv_age_s=120.0,
        has_arb_violation=False, min_quoted_size=10, min_liquidity=1,
    )
    return next(c for c in checks if c.name == "signal_still_actionable").passed


def _revalidation(armed_z: float, current_z: float) -> bool:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    return revalidate_preview(
        preview_state="valid_for_submit", preview_user_action=None,
        preview_expires_at=now + timedelta(seconds=60), now=now,
        armed_z=armed_z, current_z=current_z, z_threshold_min=THRESHOLD,
        surface_age_seconds=10.0, max_iv_age_seconds=None, current_regime="calm",
    ).passed


@pytest.mark.parametrize("factor", [0.5, 0.69, 0.7, 0.8, 0.99, 1.0, 1.2, 2.0])
def test_arm_and_submit_agree_on_every_z(factor: float) -> None:
    z = THRESHOLD * factor
    assert _preview_check(2.0, z) == _revalidation(2.0, z) == signal_still_actionable(2.0, z, THRESHOLD)
    assert _preview_check(-2.0, -z) == _revalidation(-2.0, -z)


def test_the_bar_is_the_arming_threshold_itself() -> None:
    assert SIGNAL_ACTIONABLE_FACTOR == 1.0
    assert signal_still_actionable(2.0, THRESHOLD, THRESHOLD)
    assert not signal_still_actionable(2.0, THRESHOLD * 0.99, THRESHOLD)
    assert not signal_still_actionable(2.0, -2.0, THRESHOLD), "a flipped sign is never actionable"
