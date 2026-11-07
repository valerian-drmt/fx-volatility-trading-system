"""One home for the historical VaR arithmetic (#316, audit 03#3 / 03#12).

Every VaR number the API serves goes through these helpers, so the sign, the
percentile method and the clamps are decided once:

* **sign**: a VaR / ES is a *positive USD loss* here. ``/portfolio/var`` turns
  it into a P&L quantile (negative = loss) at the edge because the desk plots
  it on the P&L axis; ``/portfolio/marginal-var`` serves the positive loss.
  Each payload names its convention (``convention`` field).
* **percentile**: linear interpolation between order statistics (numpy's
  default ``linear`` method), on the finite values only.
* **clamps**: a loss is never negative (a tail that is still a gain reads as
  0), ES is never smaller than the VaR it extends, and a diversification
  ratio lives in [0, 100] %.
* **horizon**: one trading day. Nothing here scales to longer horizons; a
  full-revaluation VaR does not obey the sqrt-t rule the old UI applied.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

# Below this the empirical quantiles are noise, whatever the method.
MIN_OBS = 5


def finite(values: Iterable[float]) -> list[float]:
    """The finite floats of ``values``, in order (NaN / inf dropped)."""
    out: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def quantile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interpolated q-quantile (q in [0, 1]) of an already-sorted list."""
    if not sorted_vals:
        return None
    q = min(1.0, max(0.0, q))
    rank = q * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (rank - lo) * (sorted_vals[hi] - sorted_vals[lo])


def loss_at(series: Iterable[float], conf: float) -> float:
    """Historical VaR at ``conf`` as a positive USD loss (0 when the tail is a gain)."""
    s = sorted(finite(series))
    q = quantile(s, 1.0 - conf)
    return max(0.0, -q) if q is not None else 0.0


@dataclass(frozen=True)
class VarSummary:
    var_95: float   # positive USD loss
    var_99: float
    es_99: float    # mean loss at or beyond the 99 % quantile, >= var_99
    n: int


def var_summary(series: Iterable[float], *, min_obs: int = MIN_OBS) -> VarSummary | None:
    """1-day VaR 95 / 99 + ES 99 from a P&L series (one value per scenario or
    session). ``None`` below ``min_obs`` finite observations."""
    s = sorted(finite(series))
    if len(s) < min_obs:
        return None
    q95 = quantile(s, 0.05)
    q99 = quantile(s, 0.01)
    assert q95 is not None and q99 is not None
    tail = [x for x in s if x <= q99]
    es = sum(tail) / len(tail) if tail else q99
    var_95 = max(0.0, -q95)
    var_99 = max(var_95, max(0.0, -q99))
    es_99 = max(var_99, max(0.0, -es))
    return VarSummary(var_95=var_95, var_99=var_99, es_99=es_99, n=len(s))


def diversification_pct(portfolio_loss: float, standalone_sum: float) -> float:
    """``1 - VaR_p / sum(standalone)`` in %, clamped to [0, 100]."""
    if standalone_sum <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - portfolio_loss / standalone_sum)))
