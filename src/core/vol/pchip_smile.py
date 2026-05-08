"""PCHIP-based smile interpolation keyed by BS delta, with optional fallback.

Given a set of (delta, iv, strike) observations at a single tenor, build
two monotone PCHIP splines (iv vs. delta, strike vs. delta) and evaluate
them at the canonical pillars (50∆, 25∆C, 25∆P, 10∆C, 10∆P).

PCHIP itself does **not** extrapolate : a target delta outside the
observed range returns ``None`` from the native spline. Callers may
inject a ``fallback`` callable (typically a calibrated SVI evaluated at
the target delta) — it is consulted only when PCHIP cannot deliver and
the requested delta is within ``max_extrapolation_distance`` of the
observed support boundary.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import PchipInterpolator

# Canonical delta pillars : ATM (0.50), 25-delta call / put, 10-delta call / put.
# Deltas follow the convention call=d, put=1-d.
DELTA_LABELS: dict[str, float] = {
    "atm": 0.50,
    "25dc": 0.25,
    "25dp": 0.75,
    "10dc": 0.10,
    "10dp": 0.90,
}


@dataclass(frozen=True)
class SmilePillar:
    iv: float | None
    strike: float | None
    source: str = "none"  # "pchip" | "svi_fallback" | "none"


# fallback signature : delta -> (iv, strike) or None
SmileFallback = Callable[[float], tuple[float, float] | None]


def interpolate_delta_pillars(
    observations: Iterable[tuple[float, float, float]],
    fallback: SmileFallback | None = None,
    max_extrapolation_distance: float = 0.10,
) -> dict[str, SmilePillar]:
    """Return ``{label: SmilePillar(iv, strike, source)}`` for every canonical pillar.

    ``observations`` is an iterable of ``(delta, iv, strike)`` triples at
    a single tenor. Duplicate or near-duplicate deltas are dropped (PCHIP
    rejects non-monotone x-values). Returns all-``none`` pillars when
    fewer than three usable observations remain.

    When a pillar's target delta falls outside the observed support and
    ``fallback`` is provided, the fallback is consulted — but only if the
    target sits within ``max_extrapolation_distance`` (in delta units) of
    the closer observed boundary. Beyond that distance, the pillar
    remains ``None`` to avoid trusting an extrapolation that is too far.
    """
    empty = {label: SmilePillar(None, None, "none") for label in DELTA_LABELS}

    pairs = sorted(observations)
    if len(pairs) < 3:
        return empty

    deltas = np.array([p[0] for p in pairs])
    ivs = np.array([p[1] for p in pairs])
    ks = np.array([p[2] for p in pairs])

    # Drop entries whose delta is too close to the previous one — PCHIP
    # requires strictly increasing x values.
    mask = np.diff(deltas, prepend=-999) > 1e-6
    deltas, ivs, ks = deltas[mask], ivs[mask], ks[mask]
    if len(deltas) < 3:
        return empty

    d_min, d_max = float(deltas[0]), float(deltas[-1])
    interp_iv = PchipInterpolator(deltas, ivs)
    interp_k = PchipInterpolator(deltas, ks)

    def _at(d: float) -> SmilePillar:
        if d_min <= d <= d_max:
            try:
                return SmilePillar(float(interp_iv(d)), float(interp_k(d)), "pchip")
            except (ValueError, TypeError):
                return SmilePillar(None, None, "none")
        if fallback is None:
            return SmilePillar(None, None, "none")
        # Distance in delta units to the closer boundary.
        distance = min(abs(d - d_min), abs(d - d_max))
        if distance > max_extrapolation_distance:
            return SmilePillar(None, None, "none")
        try:
            fb = fallback(d)
        except Exception:
            return SmilePillar(None, None, "none")
        if fb is None:
            return SmilePillar(None, None, "none")
        iv, strike = fb
        return SmilePillar(float(iv), float(strike), "svi_fallback")

    return {label: _at(d) for label, d in DELTA_LABELS.items()}
