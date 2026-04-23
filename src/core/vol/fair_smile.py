"""Fair smile — EWMA-anchored SVI params + per-pillar z-score signals.

Refactor-plan P2.3. Stores per-tenor SVI parameters historically in
Postgres (``svi_params``), computes an exponentially-weighted average
per parameter, and exposes a "fair smile" that the operator can
compare to the live smile.

Signal extension (F4 of the critique) : five scalars per tenor instead
of one. Each SVI parameter has its own historical distribution ; the
z-score of today's value vs history (excluding today) is the signal
for that parameter.

Bootstrap-safe
--------------
With < 30 historical observations the std estimator is unstable so
``compute_param_signals`` flags ``bootstrap=True`` and returns z-scores
of 0 — the dashboard can then render "accumulating history" rather
than a spurious signal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

EWMA_LAMBDA_DEFAULT = 0.94
MIN_HISTORY_FOR_SIGNAL = 30


@dataclass(frozen=True)
class FairSmileParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float


@dataclass(frozen=True)
class ParamSignal:
    param: str     # 'a' | 'b' | 'rho' | 'm' | 'sigma'
    current: float
    fair: float
    std: float
    z: float
    bootstrap: bool


def ewma_params(
    history: list[dict[str, float]], lambda_: float = EWMA_LAMBDA_DEFAULT,
) -> FairSmileParams | None:
    """Exponentially-weighted average of each SVI parameter.

    ``history`` : list of SVI parameter snapshots, oldest first. Each
    snapshot must contain keys ``a, b, rho, m, sigma`` (floats).
    Returns ``None`` if the history is empty.
    """
    if not history:
        return None
    # weight_t = (1 - λ) λ^(n-t) with most-recent getting the largest weight.
    # To keep numerically stable we accumulate both num and denom.
    acc: dict[str, float] = {"a": 0.0, "b": 0.0, "rho": 0.0, "m": 0.0, "sigma": 0.0}
    denom = 0.0
    weight = 1.0
    for snap in reversed(history):
        if weight < 1e-9:
            break
        for key in acc:
            v = snap.get(key)
            if isinstance(v, (int, float)):
                acc[key] += weight * float(v)
        denom += weight
        weight *= lambda_
    if denom <= 0:
        return None
    return FairSmileParams(
        a=acc["a"] / denom,
        b=acc["b"] / denom,
        rho=acc["rho"] / denom,
        m=acc["m"] / denom,
        sigma=acc["sigma"] / denom,
    )


def compute_param_signals(
    current: dict[str, float],
    history: list[dict[str, float]],
    min_history: int = MIN_HISTORY_FOR_SIGNAL,
) -> list[ParamSignal]:
    """Per-parameter z-score signals.

    ``history`` does NOT include ``current`` — the signal compares
    today's params against the distribution of past params. With fewer
    than ``min_history`` points the std is not reliable ; return
    z-scores of 0 flagged ``bootstrap=True``.
    """
    import statistics

    out: list[ParamSignal] = []
    bootstrap = len(history) < min_history
    for key in ("a", "b", "rho", "m", "sigma"):
        today = current.get(key)
        if not isinstance(today, (int, float)):
            continue
        values = [s.get(key) for s in history if isinstance(s.get(key), (int, float))]
        if not values:
            out.append(ParamSignal(
                param=key, current=float(today), fair=float(today),
                std=0.0, z=0.0, bootstrap=True,
            ))
            continue
        mean_ = float(statistics.mean(values))
        std_ = float(statistics.pstdev(values)) if len(values) >= 2 else 0.0
        if bootstrap or std_ <= 1e-9:
            z = 0.0
        else:
            z = (float(today) - mean_) / std_
        out.append(ParamSignal(
            param=key, current=float(today), fair=mean_, std=std_,
            z=z, bootstrap=bootstrap,
        ))
    return out


def fair_iv_at(
    strike: float, forward: float, tenor_years: float, fair: FairSmileParams,
) -> float:
    """Evaluate the fair SVI smile at a specific strike — returns decimal IV."""
    import math

    k = math.log(strike / forward)
    diff = k - fair.m
    w = fair.a + fair.b * (
        fair.rho * diff + math.sqrt(diff * diff + fair.sigma * fair.sigma)
    )
    w = max(w, 1e-12)
    return math.sqrt(w / max(tenor_years, 1e-12))


def z_score_summary(signals: list[ParamSignal]) -> dict[str, Any]:
    """Compact payload for JSON / API exposure — one entry per parameter."""
    return {
        s.param: {
            "current": round(s.current, 6),
            "fair": round(s.fair, 6),
            "std": round(s.std, 6),
            "z": round(s.z, 4),
            "bootstrap": s.bootstrap,
        }
        for s in signals
    }
