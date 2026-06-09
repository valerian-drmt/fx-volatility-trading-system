"""Feature-enrichment helpers for the Step 2 dashboard / regime gating.

Pure functions ; no DB, no IB, no Redis. Caller passes raw arrays + the
current observation, gets back enums / floats. Storage of the results is
the orchestrator's responsibility (cf. ``regime_snapshots`` columns added
by migration 018).

Spec : ``docs/vol_trading_pca/specs/STEP1_REGIME_GATING.md`` §13 +
in-conversation E1 brief (5 columns + synthesis row).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np

# ─────────────────────────────────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────────────────────────────────

Bucket = Literal["--", "-", "0", "+", "++"]
Signal = Literal["noise", "weak", "strong", "tail"]
DeltaLabel = Literal["underpriced", "overpriced", "aligned"]
FeatureName = Literal["vol_level", "vol_of_vol", "term_slope"]

ALL_BUCKETS: tuple[Bucket, ...] = ("--", "-", "0", "+", "++")
ALL_SIGNALS: tuple[Signal, ...] = ("noise", "weak", "strong", "tail")


# ─────────────────────────────────────────────────────────────────────────
# 1. bucket — empirical-quantile discretisation
# ─────────────────────────────────────────────────────────────────────────

def bucket(z: float, z_history: Sequence[float]) -> Bucket:
    """Discretise the current ``z`` against its 90-day history into 5 buckets.

    Quantile cutoffs (per spec) :
        q025  = 2.5 %   → ``--`` below
        q160  = 16.0 %  → ``-``  in [q025, q160]
        q840  = 84.0 %  → ``0``  in (q160, q840) — strict open interval per spec
        q975  = 97.5 %  → ``+``  in [q840, q975)
                          ``++`` ≥ q975

    With <10 observations the empirical quantiles are too noisy ; we fall
    back to the standard-normal cutoffs (z=±1.0 / ±2.0) so the function
    always produces a sane label even at cold start.
    """
    if not z_history or not np.all(np.isfinite(z_history)):
        finite = [v for v in z_history if np.isfinite(v)]
    else:
        finite = list(z_history)

    if len(finite) < 10:
        return _bucket_normal_fallback(z)

    arr = np.asarray(finite, dtype=float)
    q025, q160, q840, q975 = np.quantile(arr, [0.025, 0.160, 0.840, 0.975])
    if z <= q025:
        return "--"
    if z <= q160:
        return "-"
    if z < q840:
        return "0"
    if z < q975:
        return "+"
    return "++"


def _bucket_normal_fallback(z: float) -> Bucket:
    """Standard-normal cutoffs : z=±1 (~16/84 %) and z=±2 (~2.5/97.5 %)."""
    if z <= -2.0:
        return "--"
    if z <= -1.0:
        return "-"
    if z < 1.0:
        return "0"
    if z < 2.0:
        return "+"
    return "++"


# ─────────────────────────────────────────────────────────────────────────
# 2. delta_z_1h — OLS slope of z over the last 60 minutes
# ─────────────────────────────────────────────────────────────────────────

def delta_z_1h(
    timestamps_minutes: Sequence[float],
    z_values: Sequence[float],
    *,
    min_points: int = 12,
) -> float | None:
    """OLS slope of ``z`` against time-in-hours over the last 60 minutes.

    Inputs
    ------
    timestamps_minutes : minutes since some arbitrary origin (we only use
        relative deltas). Must be aligned with ``z_values``.
    z_values : z-scores at those timestamps.
    min_points : refuse to fit below this. Default 12 = 12 × 5-min ticks
        ≈ 1 hour of cycles.

    Returns ``None`` when there are too few points or the design matrix is
    rank-deficient (all timestamps identical). The orchestrator displays
    ``"—"`` in that case.

    The slope unit is **z-points per hour**.
    """
    if len(timestamps_minutes) != len(z_values):
        raise ValueError("timestamps_minutes and z_values must have the same length")
    if len(z_values) < min_points:
        return None
    t = np.asarray(timestamps_minutes, dtype=float) / 60.0   # → hours
    y = np.asarray(z_values, dtype=float)
    finite = np.isfinite(t) & np.isfinite(y)
    if finite.sum() < min_points:
        return None
    t = t[finite]
    y = y[finite]
    # Centre to improve numerical conditioning.
    t_centered = t - t.mean()
    denom = float(np.dot(t_centered, t_centered))
    if denom < 1e-12:
        return None
    slope = float(np.dot(t_centered, y - y.mean()) / denom)
    return slope


# ─────────────────────────────────────────────────────────────────────────
# 3. pct — empirical percentile of the current value
# ─────────────────────────────────────────────────────────────────────────

def pct(value: float, value_history: Sequence[float]) -> int | None:
    """Percentile of ``value`` within ``value_history`` (0-100, integer).

    Operates on the **value**, not the z-score — the percentile reveals
    distribution asymmetry that the standardised z-score hides.

    Returns ``None`` when ``value_history`` is empty (caller renders "—").
    """
    finite = [v for v in value_history if np.isfinite(v)]
    if not finite:
        return None
    arr = np.asarray(finite, dtype=float)
    # ``mean`` matches scipy.stats.percentileofscore(kind='mean'),
    # which is the average of strict and weak rank counts.
    n = arr.size
    strict = int(np.sum(arr < value))
    weak = int(np.sum(arr <= value))
    rank = (strict + weak) / 2.0
    return round(100.0 * rank / n)


# ─────────────────────────────────────────────────────────────────────────
# 4. signal — qualitative strength classifier
# ─────────────────────────────────────────────────────────────────────────

def signal(z: float, pct_value: int | None) -> Signal:
    """Classify ``(z, pct)`` into noise / weak / strong / tail.

    Resolved rules (the spec text contained a contradiction with the
    acceptance cases ; the cases win) :
        * ``|z| < 1``                         → noise
        * ``|z| < 1.5``                       → weak
        * ``|z| < 2.5`` AND pct ∈ (1, 99)     → strong
        * else                                → tail

    A ``None`` pct is interpreted as "no opinion on extremity" : we keep
    the strong / tail split based on z alone (i.e. unknown pct on a
    |z|=1.6 input still classifies as tail rather than strong, which is
    the conservative call when history is missing).
    """
    az = abs(z)
    if az < 1.0:
        return "noise"
    if az < 1.5:
        return "weak"
    if az < 2.5 and pct_value is not None and 1 < pct_value < 99:
        return "strong"
    return "tail"


# ─────────────────────────────────────────────────────────────────────────
# 5. interpret_delta — verbal label for (z_observed - expected_mu)
# ─────────────────────────────────────────────────────────────────────────

# Per-feature semantic of the sign of ``delta = z_obs - expected_mu``.
# +1 means "delta > 0  →  underpriced" (the standard convention).
# -1 means the inverse : for ``term_slope`` a more negative observed z
# indicates the market pricing more risk on the short tenor, so the
# "underpricing" interpretation flips.
_DELTA_SIGN_BY_FEATURE: dict[str, int] = {
    "vol_level": +1,
    "vol_of_vol": +1,
    "term_slope": -1,
}

# Threshold (in σ units) for flipping out of "aligned".
_DELTA_THRESHOLD = 0.30


def interpret_delta(feature: str, delta: float) -> DeltaLabel:
    """Translate a ``z_obs - expected_mu`` delta into ``underpriced`` /
    ``overpriced`` / ``aligned``, with the per-feature sign convention.

    Caller supplies the raw delta in σ units. We multiply by the
    feature-specific sign so the verbal label is comparable across the
    three features (``term_slope`` flips because more-negative = more risk
    priced on the short tenor).
    """
    sign = _DELTA_SIGN_BY_FEATURE.get(feature, +1)
    eff = sign * delta
    if eff > _DELTA_THRESHOLD:
        return "underpriced"
    if eff < -_DELTA_THRESHOLD:
        return "overpriced"
    return "aligned"
