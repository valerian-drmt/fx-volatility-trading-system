"""Compose the ``/api/v1/regime/features`` payload.

Pulls the latest ``regime_snapshots`` row, derives the 5 enrichment columns
on the fly from the 90-day history (E1 helpers), looks up the joint-pattern
regime from ``regime_lookup_table`` and the per-feature baseline from
``vol_features_context_baseline``, then assembles the synthesis line
(joint_pattern · regime · dominant · vs_expected · action).

When the E3 batch has stamped the columns (``bucket_<f>``, ``signal_<f>``,
…), we use them as-is. Otherwise we recompute from history so the endpoint
works the moment migration 018 lands, even before E3.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.vol.feature_enrichment import (
    Bucket,
    Signal,
    interpret_delta,
)
from core.vol.feature_enrichment import (
    bucket as compute_bucket,
)
from core.vol.feature_enrichment import (
    delta_z_1h as compute_delta_z_1h,
)
from core.vol.feature_enrichment import (
    pct as compute_pct,
)
from core.vol.feature_enrichment import (
    signal as compute_signal,
)
from persistence.models import (
    Event,
    RegimeSnapshot,
)

logger = logging.getLogger(__name__)

FEATURES: tuple[str, ...] = ("vol_level", "vol_of_vol", "term_slope")
HISTORY_DAYS = 90
DELTA_WINDOW_MIN = 60
CRITICAL_PATTERNS: frozenset[str] = frozenset({
    "(++,++,--)", "(++,++,-)", "(--,++,++)",
})


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────

async def build_features_payload(
    db: AsyncSession, symbol: str = "EURUSD",
) -> dict[str, Any] | None:
    """Return the full payload, or ``None`` if no regime_snapshot exists."""
    latest = (await db.execute(
        select(RegimeSnapshot)
        .where(RegimeSnapshot.symbol == symbol)
        .order_by(desc(RegimeSnapshot.timestamp))
        .limit(1)
    )).scalar_one_or_none()
    if latest is None:
        return None

    history = (await db.execute(
        select(RegimeSnapshot)
        .where(RegimeSnapshot.symbol == symbol)
        .where(RegimeSnapshot.timestamp >= latest.timestamp - timedelta(days=HISTORY_DAYS))
        .where(RegimeSnapshot.timestamp <= latest.timestamp)
        .order_by(RegimeSnapshot.timestamp)
    )).scalars().all()

    feature_rows: list[dict[str, Any]] = []
    for f in FEATURES:
        feature_rows.append(await _build_feature_row(db, latest, history, f))

    synthesis = await _build_synthesis(db, feature_rows, latest)
    return {
        "timestamp": latest.timestamp,
        "symbol": symbol,
        "features": feature_rows,
        "synthesis": synthesis,
    }


# ─────────────────────────────────────────────────────────────────────────
# Per-feature row
# ─────────────────────────────────────────────────────────────────────────

async def _build_feature_row(
    db: AsyncSession,
    latest: RegimeSnapshot,
    history: list[RegimeSnapshot],
    feature: str,
) -> dict[str, Any]:
    """One row of the features table : value, z, bucket, Δz/1h, pct, signal,
    expected_z context."""
    value_attr = f"{feature}_pct"
    z_attr = f"{feature}_z"
    value = _f(getattr(latest, value_attr, None))
    z = _f(getattr(latest, z_attr, None))

    # Prefer stamped columns (E3) ; fall back to live computation.
    bk = getattr(latest, f"bucket_{feature}", None)
    pct_v = getattr(latest, f"pct_{feature}", None)
    sig = getattr(latest, f"signal_{feature}", None)
    dz = getattr(latest, f"delta_z_1h_{feature}", None)

    if bk is None and z is not None:
        z_history = [_f(getattr(r, z_attr, None)) for r in history]
        z_history = [v for v in z_history if v is not None]
        bk = compute_bucket(z, z_history)
    if pct_v is None and value is not None:
        v_history = [_f(getattr(r, value_attr, None)) for r in history]
        v_history = [v for v in v_history if v is not None]
        pct_v = compute_pct(value, v_history)
    if sig is None and z is not None:
        sig = compute_signal(z, pct_v)
    if dz is None and z is not None:
        # Last 60 minutes ; minutes since latest.timestamp.
        cutoff = latest.timestamp - timedelta(minutes=DELTA_WINDOW_MIN)
        recent = [r for r in history if r.timestamp >= cutoff]
        if len(recent) >= 12:
            t_min = [
                (r.timestamp - recent[0].timestamp).total_seconds() / 60.0
                for r in recent
            ]
            zs = [_f(getattr(r, z_attr, None)) for r in recent]
            zs_clean = [v if v is not None else 0.0 for v in zs]
            dz = compute_delta_z_1h(t_min, zs_clean)

    expected = _lookup_baseline(feature, latest, history)
    vs_expected_label = None
    if z is not None and expected and expected["status"] in ("valid", "approx") \
            and expected.get("mu") is not None:
        vs_expected_label = interpret_delta(feature, z - expected["mu"])

    return {
        "name": feature,
        "value": _round(value, 2),
        "z": _round(z, 2),
        "bucket": bk,
        "delta_z_1h": _round(_f(dz), 2),
        "pct": pct_v,
        "signal": sig,
        "expected_z": expected,
        "vs_expected": vs_expected_label,
    }


# ─────────────────────────────────────────────────────────────────────────
# Synthesis row
# ─────────────────────────────────────────────────────────────────────────

async def _build_synthesis(
    db: AsyncSession,
    feature_rows: list[dict[str, Any]],
    latest: RegimeSnapshot,
) -> dict[str, Any]:
    by_name = {row["name"]: row for row in feature_rows}
    bk_l = by_name["vol_level"]["bucket"]
    bk_v = by_name["vol_of_vol"]["bucket"]
    bk_s = by_name["term_slope"]["bucket"]
    if not (bk_l and bk_v and bk_s):
        joint_pattern = None
        regime_payload = None
    else:
        joint_pattern = f"({bk_l},{bk_v},{bk_s})"
        regime_payload = _lookup_regime(joint_pattern)

    # Dominant feature = argmax |z|.
    z_by_feature: dict[str, float] = {}
    for row in feature_rows:
        z = row["z"]
        if z is not None:
            z_by_feature[row["name"]] = abs(float(z))
    dominant = max(z_by_feature, key=z_by_feature.get) if z_by_feature else None

    vs_expected_payload: dict[str, Any] | None = None
    if dominant:
        dom_row = by_name[dominant]
        z = dom_row["z"]
        exp = dom_row["expected_z"]
        if z is not None and exp and exp.get("status") in ("valid", "approx") \
                and exp.get("mu") is not None:
            delta = float(z) - exp["mu"]
            label = interpret_delta(dominant, delta)
            vs_expected_payload = {
                "feature": dominant,
                "delta_sigma": _round(delta, 2),
                "label": label,
            }

    action = _build_action(
        joint_pattern=joint_pattern,
        regime=regime_payload,
        dominant_signal=by_name[dominant]["signal"] if dominant else None,
        vs_expected_label=vs_expected_payload["label"] if vs_expected_payload else None,
        event_dampener=bool(latest.event_dampener),
    )
    return {
        "joint_pattern": joint_pattern,
        "regime": regime_payload,
        "dominant": dominant,
        "vs_expected": vs_expected_payload,
        "action": action,
    }


def _build_action(
    *,
    joint_pattern: str | None,
    regime: dict[str, Any] | None,
    dominant_signal: str | None,
    vs_expected_label: str | None,
    event_dampener: bool,
) -> str:
    """Decision tree from the E1 brief — base × modifier."""
    base = "size × 0.5" if event_dampener else "size × 1.0"
    if vs_expected_label == "underpriced" and dominant_signal == "strong":
        modifier = "asymmetric calendar"
    elif dominant_signal == "tail":
        modifier = "alert"
    elif joint_pattern in CRITICAL_PATTERNS:
        modifier = "stop"
    elif regime is not None and regime.get("regime_name") == "unmapped_extreme":
        modifier = "alert"
    else:
        modifier = "monitor"
    return f"{base} + {modifier}"


# ─────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────

def _lookup_regime(pattern: str) -> dict[str, Any]:
    """Pattern → regime metadata. Sourced from
    ``core.regime_patterns.REGIME_PATTERNS`` (was a ``regime_pattern_dict``
    DB table until migration 039 dropped that mirror). Always returns a
    row — falls back to ``unmapped_extreme`` when the pattern is unseen.
    """
    from core.regime_patterns import lookup_regime
    row = lookup_regime(pattern)
    return {
        "id": row["regime_id"],
        "name": row["regime_name"],
        "family": row["family"],
        "action_default": row["action_default"],
        "asymmetry_note": row["asymmetry_note"],
        "intensity_count": row["intensity_count"],
    }


_DAYS_BUCKETS: tuple[tuple[int, int, int], ...] = (
    (0, 1, 0), (2, 3, 1), (4, 5, 2), (6, 10, 3),
)


def _days_bucket(days_to_event: float | None) -> int:
    if days_to_event is None:
        return 4
    d = int(days_to_event)
    for lo, hi, idx in _DAYS_BUCKETS:
        if lo <= d <= hi:
            return idx
    return 4


def _tod_bucket(ts: datetime) -> str:
    h = ts.hour if ts.tzinfo is None else ts.astimezone(UTC).hour
    if 7 <= h < 12:
        return "london_open"
    if 12 <= h < 16:
        return "overlap"
    if 16 <= h < 22:
        return "ny_close"
    return "asia"


_MIN_OBS_VALID: int = 20  # threshold for "valid" baseline status


def _lookup_baseline(
    feature: str,
    latest: RegimeSnapshot,
    history: list[RegimeSnapshot],
) -> dict[str, Any] | None:
    """Return ``{mu, sigma, n_obs, status, context, relaxation}`` for ``feature``.

    Computes μ/σ live from ``history`` with progressive context relaxation :
    ``exact`` -> ``event_days`` -> ``event`` -> ``unconditional``. Status
    ``valid`` requires ≥ 20 obs ; below that we still return the computed
    values with status ``approx`` so downstream gets a number instead of "—".
    Unconditional level always has plenty of obs (≈ 90d worth of snapshots)
    so we never hand back an empty baseline.
    """
    event_type = (latest.next_event_type or "none").upper() if latest.next_event_type else "none"
    days_bucket = _days_bucket(_f(latest.days_to_next_event))
    tod_bucket = _tod_bucket(latest.timestamp)
    base_context = {
        "event_type": event_type,
        "days_bucket": days_bucket,
        "tod_bucket": tod_bucket,
    }

    # Live computation : progressive relaxation across context dimensions
    # (no pre-computed baseline table — the relaxation below covers the
    # same use cases without an extra batch job).
    z_attr = f"{feature}_z"

    def _z_values(filt: list[str]) -> list[float]:
        out: list[float] = []
        for r in history:
            if "event_type" in filt:
                r_event = (r.next_event_type or "none").upper() if r.next_event_type else "none"
                if r_event != event_type:
                    continue
            if "days_bucket" in filt and _days_bucket(_f(r.days_to_next_event)) != days_bucket:
                continue
            if "tod_bucket" in filt and _tod_bucket(r.timestamp) != tod_bucket:
                continue
            v = _f(getattr(r, z_attr, None))
            if v is not None:
                out.append(v)
        return out

    levels: list[tuple[str, list[str]]] = [
        ("exact",          ["event_type", "days_bucket", "tod_bucket"]),
        ("event_days",     ["event_type", "days_bucket"]),
        ("event",          ["event_type"]),
        ("unconditional",  []),
    ]
    for label, filt in levels:
        zs = _z_values(filt)
        n = len(zs)
        if n == 0:
            continue
        mean = sum(zs) / n
        var = sum((v - mean) ** 2 for v in zs) / n if n > 1 else 0.0
        std = var ** 0.5
        if n >= _MIN_OBS_VALID or label == "unconditional":
            return {
                "mu": float(mean),
                "sigma": float(std),
                "n_obs": int(n),
                "status": "valid" if n >= _MIN_OBS_VALID else "approx",
                "context": base_context,
                "relaxation": label,
            }

    # 3. Not even unconditional history — degenerate (cold-start).
    return {
        "mu": 0.0, "sigma": 1.0, "n_obs": 0, "status": "approx",
        "context": base_context, "relaxation": "cold_start",
    }


# ─────────────────────────────────────────────────────────────────────────
# Misc
# ─────────────────────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _round(v: float | None, ndigits: int) -> float | None:
    return None if v is None else round(v, ndigits)


# Re-exports for typing convenience.
__all__ = [
    "Bucket",
    "Event",
    "Signal",
    "build_features_payload",
]
