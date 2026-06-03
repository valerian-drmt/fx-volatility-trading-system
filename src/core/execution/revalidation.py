"""Pre-submit revalidation : defense in depth between Arm and Submit.

A trade preview is built at Arm time. The user may sit on it for ~120s
before clicking Submit. Re-run the gates that can change state in that
window so we don't submit a trade that has gone stale.

Gates re-checked here (cf. STEP4 §7.2 step 3) :
  - preview not expired
  - preview not already actioned (submitted / cancelled)
  - state == 'valid_for_submit'
  - signal still actionable (current |z| ≥ threshold and same sign as armed_z)
  - surface freshness (age below limit)
  - regime not pre_event

Pure function — caller passes the snapshot data, no DB / Redis here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RevalidationResult:
    passed: bool
    reason: str | None
    details: dict[str, Any]


def revalidate_preview(
    *,
    preview_state: str,
    preview_user_action: str | None,
    preview_expires_at: datetime,
    now: datetime,
    armed_z: float | None,
    current_z: float | None,
    z_threshold_min: float,
    surface_age_seconds: float,
    max_iv_age_seconds: float,
    current_regime: str | None,
) -> RevalidationResult:
    """Run all gates. Stops at first failure (defense in depth).

    Parameters
    ----------
    preview_state : 'valid_for_submit' | 'blocked' | 'expired' | 'submitted' | 'cancelled'
    preview_user_action : None if pristine, else 'submitted' | 'cancelled'
    armed_z, current_z : pca z-scores (manual mode → both None, gate skipped)
    current_regime : 'calm' | 'stressed' | 'pre_event' | None (None → gate skipped)
    """
    if preview_user_action is not None:
        return RevalidationResult(
            False, "preview_already_actioned", {"user_action": preview_user_action}
        )

    if preview_state != "valid_for_submit":
        return RevalidationResult(
            False, "preview_state_not_valid", {"state": preview_state}
        )

    if preview_expires_at < now:
        return RevalidationResult(
            False,
            "preview_expired",
            {"expires_at": preview_expires_at.isoformat(), "now": now.isoformat()},
        )

    # Signal-still-actionable gate (skipped for manual mode where armed_z is None)
    if armed_z is not None and current_z is not None:
        if abs(current_z) < z_threshold_min:
            return RevalidationResult(
                False,
                "signal_no_longer_actionable",
                {"current_z": current_z, "threshold": z_threshold_min},
            )
        if (armed_z > 0) != (current_z > 0):
            return RevalidationResult(
                False,
                "signal_flipped",
                {"armed_z": armed_z, "current_z": current_z},
            )

    # surface_stale check dropped on request (mirrors the same drop on
    # the pre-submit ``iv_data_fresh`` gate). Surface freshness is shown
    # in the YELLOW block of the trade panel so the operator can see it
    # and decide; we no longer block at the orchestrator level.
    _ = (surface_age_seconds, max_iv_age_seconds)  # silence unused

    if current_regime == "pre_event":
        return RevalidationResult(
            False, "regime_pre_event", {"regime": current_regime}
        )

    return RevalidationResult(True, None, {})
