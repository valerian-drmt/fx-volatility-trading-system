"""Stamp the 5 enrichment columns on a fresh ``snapshot_row``.

Pure helper — caller passes the value/z and 90-day history per feature,
gets back the dict augmented with ``bucket_<f>``, ``pct_<f>``,
``signal_<f>`` and ``delta_z_1h_<f>`` keys for each of the three
features. Persistence layer simply spreads the dict into the INSERT.

Called by ``engines.vol.engine._compute_regime`` after
``compute_regime_snapshot`` returns. Keeping it pure means the unit tests
in E1 already cover the math ; this module only wires keys.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from core.vol.feature_enrichment import (
    bucket as _bucket,
)
from core.vol.feature_enrichment import (
    delta_z_1h as _delta_z_1h,
)
from core.vol.feature_enrichment import (
    pct as _pct,
)
from core.vol.feature_enrichment import (
    signal as _signal,
)

FEATURES: tuple[str, ...] = ("vol_level", "vol_of_vol", "term_slope")
DELTA_WINDOW_MIN = 60


def stamp_enrichment(
    snapshot_row: dict[str, Any],
    *,
    z_history: dict[str, Sequence[float]],
    value_history: dict[str, Sequence[float]],
    recent_z: dict[str, Sequence[tuple[datetime, float]]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a *new* dict — original ``snapshot_row`` is not mutated.

    Parameters
    ----------
    snapshot_row : dict produced by ``compute_regime_snapshot`` (carries
        ``vol_level_pct``, ``vol_level_z`` …).
    z_history : per-feature 90-day z-score series (lists of floats).
    value_history : per-feature 90-day value series (lists of floats).
    recent_z : per-feature ``(timestamp, z)`` pairs covering the last hour
        — feeds ``delta_z_1h``. Optional ; ``None`` skips the slope.
    now : reference instant for the slope window. Defaults to "max ts in
        recent_z" when present.
    """
    out = dict(snapshot_row)
    for f in FEATURES:
        z_attr = f"{f}_z"
        v_attr = f"{f}_pct"
        z_now = _to_float(out.get(z_attr))
        v_now = _to_float(out.get(v_attr))

        bk = (
            _bucket(z_now, list(z_history.get(f) or ()))
            if z_now is not None else None
        )
        pct_v = (
            _pct(v_now, list(value_history.get(f) or ()))
            if v_now is not None else None
        )
        sig = _signal(z_now, pct_v) if z_now is not None else None

        dz: float | None = None
        if recent_z is not None and z_now is not None:
            window = list(recent_z.get(f) or ())
            if window:
                ref_now = now or max(t for t, _ in window)
                cutoff = ref_now - timedelta(minutes=DELTA_WINDOW_MIN)
                trimmed = [(t, z) for t, z in window if t >= cutoff]
                if len(trimmed) >= 12:
                    base = trimmed[0][0]
                    t_min = [
                        (t - base).total_seconds() / 60.0 for t, _ in trimmed
                    ]
                    zs = [z for _, z in trimmed]
                    dz = _delta_z_1h(t_min, zs)

        out[f"bucket_{f}"] = bk
        out[f"pct_{f}"] = pct_v
        out[f"signal_{f}"] = sig
        out[f"delta_z_1h_{f}"] = dz
    return out


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
