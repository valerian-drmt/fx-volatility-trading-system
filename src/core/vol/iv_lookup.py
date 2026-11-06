"""One IV accessor for the published surface shape (#310, audit QM-02 / R3-02).

The vol engine publishes ``{tenor: {label: {"iv": σ, "strike": K, ...}}}`` with
labels ``10dp 25dp atm 25dc 10dc`` and σ as a decimal. Three consumers used to
read it three ways: ``interpolate_iv`` read a retired flat schema and always
returned None (so live re-pricing and the delta hedge never ran), the risk
engine's ``_iv_for`` dropped interpolated pillars because their strike is
None (→ 8 % fallback), and the stress panel had its own proxy. This module is
the single lookup they all call.

Pure: standard library only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from statistics import NormalDist
from typing import Any

LABELS: tuple[str, ...] = ("10dp", "25dp", "atm", "25dc", "10dc")
# Call-delta of each pillar (put pillars expressed as the call delta of the
# same strike: a 25Δ put strike has a 75Δ call).
_CALL_DELTA: dict[str, float] = {"10dp": 0.90, "25dp": 0.75, "atm": 0.50, "25dc": 0.25, "10dc": 0.10}
_N = NormalDist()


@dataclass(frozen=True)
class IvResult:
    iv: float       # decimal, e.g. 0.071
    source: str     # pillar | interp_strike | wing_clamp | single_pillar
    n_points: int   # pillars the lookup had to work with


def strike_from_delta(F: float, T: float, sigma: float, label: str) -> float:
    """Black-76 strike for a pillar's delta at forward ``F`` (no rates, as the
    rest of core.pricing). ``atm`` is the forward. Used for pillars that carry
    an IV but no strike (interpolated tenors)."""
    if label == "atm" or T <= 0 or sigma <= 0:
        return F
    call_delta = _CALL_DELTA[label]
    d1 = _N.inv_cdf(call_delta)
    vol_t = sigma * math.sqrt(T)
    return F * math.exp(0.5 * vol_t * vol_t - d1 * vol_t)


def _points(
    pillars: dict[str, Any], F: float | None, T: float | None,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for label in LABELS:
        node = pillars.get(label)
        if not isinstance(node, dict):
            continue
        iv = node.get("iv")
        if not isinstance(iv, (int, float)) or iv <= 0:
            continue
        strike = node.get("strike")
        if not isinstance(strike, (int, float)) or strike <= 0:
            if F is None or T is None or F <= 0:
                continue
            strike = strike_from_delta(float(F), float(T), float(iv), label)
        pts.append((float(strike), float(iv)))
    pts.sort(key=lambda p: p[0])
    return pts


def iv_at_strike(
    surface: dict[str, Any] | None,
    tenor: str,
    strike: float,
    *,
    F: float | None = None,
    T: float | None = None,
) -> IvResult | None:
    """IV at ``strike`` on the ``tenor`` smile: linear in strike between the two
    bracketing pillars, flat beyond the wings. ``F`` and ``T`` let a pillar
    without a strike (interpolated tenor) be placed from its delta.

    None when the tenor is absent or no pillar can be placed: callers count
    that as *missing* and must not substitute a constant silently."""
    if not isinstance(surface, dict):
        return None
    pillars = surface.get(tenor)
    if not isinstance(pillars, dict):
        return None
    pts = _points(pillars, F, T)
    if not pts:
        return None
    n = len(pts)
    if n == 1:
        return IvResult(pts[0][1], "single_pillar", n)
    if strike <= pts[0][0]:
        return IvResult(pts[0][1], "pillar" if strike == pts[0][0] else "wing_clamp", n)
    if strike >= pts[-1][0]:
        return IvResult(pts[-1][1], "pillar" if strike == pts[-1][0] else "wing_clamp", n)
    for (k0, v0), (k1, v1) in pairwise(pts):
        if strike == k0:
            return IvResult(v0, "pillar", n)
        if k0 < strike < k1:
            w = (strike - k0) / (k1 - k0)
            return IvResult(v0 + w * (v1 - v0), "interp_strike", n)
    return IvResult(pts[-1][1], "pillar", n)
