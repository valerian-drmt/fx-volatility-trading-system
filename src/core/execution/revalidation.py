"""Pre-submit revalidation : defense in depth between Arm and Submit.

A trade preview is built at Arm time. The user may sit on it for ~120s
before clicking Submit. Re-run the gates that can change state in that
window so we don't submit a trade that has gone stale.

Gates re-checked here (cf. STEP4 §7.2 step 3) :
  - preview not expired
  - preview not already actioned (submitted / cancelled)
  - state == 'valid_for_submit'
  - signal still actionable (current |z| ≥ threshold and same sign as armed_z)
  - surface freshness (age below limit, fails closed when the age is unknown;
    the caller passes ``max_iv_age_seconds=None`` to turn the gate off, which
    the submit path does outside live mode, mirroring the pre-trade gate #315)
  - regime not pre_event

Pure function — caller passes the snapshot data, no DB / Redis here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# The one factor both gates apply to ``z_threshold_min`` (#314, audit 02#13):
# a signal is still actionable while |z| >= SIGNAL_ACTIONABLE_FACTOR x threshold.
# The pre-submit check used 0.7 and the revalidation 1.0, so a preview could
# pass at Arm and be refused at Submit on the same z. 1.0 is the contract:
# the signal that armed must still clear the arming bar when it is submitted.
SIGNAL_ACTIONABLE_FACTOR: float = 1.0


def signal_still_actionable(armed_z: float, current_z: float, threshold_min: float) -> bool:
    """Same sign as the armed signal and |z| still at or above the bar."""
    if (armed_z > 0) != (current_z > 0):
        return False
    return abs(current_z) >= threshold_min * SIGNAL_ACTIONABLE_FACTOR


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
    surface_age_seconds: float | None,
    max_iv_age_seconds: float | None,
    current_regime: str | None,
) -> RevalidationResult:
    """Run all gates. Stops at first failure (defense in depth).

    Parameters
    ----------
    preview_state : 'valid_for_submit' | 'blocked' | 'expired' | 'submitted' | 'cancelled'
    preview_user_action : None if pristine, else 'submitted' | 'cancelled'
    armed_z, current_z : pca z-scores (manual mode → both None, gate skipped)
    current_regime : 'calm' | 'stressed' | 'pre_event' | None (None → gate skipped)
    surface_age_seconds : age of the surface the preview was priced on; None = unknown
    max_iv_age_seconds : the limit; None = surface gate off (non-live modes)
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
        if (armed_z > 0) != (current_z > 0):
            return RevalidationResult(
                False,
                "signal_flipped",
                {"armed_z": armed_z, "current_z": current_z},
            )
        if not signal_still_actionable(armed_z, current_z, z_threshold_min):
            return RevalidationResult(
                False,
                "signal_no_longer_actionable",
                {"current_z": current_z, "threshold": z_threshold_min * SIGNAL_ACTIONABLE_FACTOR},
            )

    # Surface freshness (audit EX-08, #339): a preview priced on a surface
    # older than the limit does not submit, and an unknown age is not a pass.
    # The caller turns the gate off (``max_iv_age_seconds=None``) outside
    # live mode, the same scope as the pre-trade gate's ``surface_stale``.
    # The checks below are independent of each other: all the ones that fail
    # are reported together (``details["reasons"]``), so the trader does not
    # fix one and meet the next (#480). ``reason`` stays the first of them.
    failed: list[tuple[str, dict[str, Any]]] = []
    if max_iv_age_seconds is not None:
        if surface_age_seconds is None:
            failed.append(("surface_age_unknown", {"max_iv_age_seconds": max_iv_age_seconds}))
        elif surface_age_seconds > max_iv_age_seconds:
            failed.append((
                "surface_stale",
                {"surface_age_seconds": surface_age_seconds, "max_iv_age_seconds": max_iv_age_seconds},
            ))
    if current_regime == "pre_event":
        failed.append(("regime_pre_event", {"regime": current_regime}))
    if failed:
        details: dict[str, Any] = {}
        for _, d in failed:
            details.update(d)
        details["reasons"] = [r for r, _ in failed]
        return RevalidationResult(False, failed[0][0], details)

    return RevalidationResult(True, None, {})
