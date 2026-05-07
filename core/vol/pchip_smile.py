"""PCHIP-based smile interpolation keyed by BS delta.

Given a set of (delta, iv, strike) observations at a single tenor, build
two monotone PCHIP splines (iv vs. delta, strike vs. delta) and evaluate
them at the canonical pillars (50∆, 25∆C, 25∆P, 10∆C, 10∆P). Extrapolation
is disabled : requests outside the observed delta range return ``None``.
"""
from __future__ import annotations

from collections.abc import Iterable
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


def interpolate_delta_pillars(
    observations: Iterable[tuple[float, float, float]],
) -> dict[str, SmilePillar]:
    """Return {label: SmilePillar(iv, strike)} for every label in ``DELTA_LABELS``.

    ``observations`` is an iterable of ``(delta, iv, strike)`` triples at a
    single tenor. Duplicate or near-duplicate deltas are dropped (PCHIP
    rejects non-monotone x-values) ; the function returns all-``None``
    pillars when fewer than three usable observations remain.
    """
    pairs = sorted(observations)
    if len(pairs) < 3:
        return {label: SmilePillar(None, None) for label in DELTA_LABELS}

    deltas = np.array([p[0] for p in pairs])
    ivs = np.array([p[1] for p in pairs])
    ks = np.array([p[2] for p in pairs])

    # Drop entries whose delta is too close to the previous one — PCHIP
    # requires strictly increasing x values.
    mask = np.diff(deltas, prepend=-999) > 1e-6
    deltas, ivs, ks = deltas[mask], ivs[mask], ks[mask]
    if len(deltas) < 3:
        return {label: SmilePillar(None, None) for label in DELTA_LABELS}

    d_min, d_max = float(deltas[0]), float(deltas[-1])
    interp_iv = PchipInterpolator(deltas, ivs)
    interp_k = PchipInterpolator(deltas, ks)

    def _at(d: float) -> SmilePillar:
        if d < d_min or d > d_max:
            return SmilePillar(None, None)
        try:
            return SmilePillar(float(interp_iv(d)), float(interp_k(d)))
        except (ValueError, TypeError):
            return SmilePillar(None, None)

    return {label: _at(d) for label, d in DELTA_LABELS.items()}
