"""Historical-simulation VaR over the *current* book (R11 G-risk).

The account-P&L method (net-liq day-over-day deltas) needs the account itself to
have existed for a while: 5 daily observations minimum, 500+ for a stable tail.
On a freshly seeded deployment it simply has nothing to say — and what it does
say describes yesterday's book, not today's.

This module replays ~1 year of **real EURUSD daily moves through today's book**
instead. Each historical session d contributes one scenario ``(Δspot, Δvol)``;
the book is fully BS-revalued under it (``core.risk.stress.reval_book``) and the
resulting P&L vector *is* the empirical distribution the VaR/ES quantiles and the
histogram are read off. It works from the first minute a position is open, and it
measures the risk actually on the book right now.

Shock construction, per historical session d:

  - ``Δspot``  = the realised close-to-close return, in bp.
  - ``Δvol``   = the change in the trailing-``RV_WINDOW`` annualised close-to-close
    realised vol, in vol points. **This is a proxy**: we hold no year of implied-vol
    history, so realised vol stands in for it. Two properties make it a usable one —
    a 21-session window moves ~0.2–0.5 vol pt/day, the right order of magnitude for
    1M ATM implied, and because ``Δvol_d`` is computed on a window *containing*
    ``r_d`` it carries the empirical spot/vol co-movement (a big down day arrives
    with a vol bid), which is what vanna and the skew wing are exposed to. It is a
    stylised desk model, not a smile recalibration — same footing as the documented
    shocks in ``core.risk.var_factors``.

Everything here is pure (no I/O, no numpy): the caller supplies the closes and the
resolved book.
"""
from __future__ import annotations

from typing import Any

from core.risk.stress import reval_book

# Trailing sessions in the realised-vol window used for the Δvol proxy. 21 ≈ one
# calendar month, matching the tenor the ATM level shock is meant to stand for.
RV_WINDOW = 21
# Sessions per year — annualises the realised vol and matches the √t scaling the
# VaR table applies to longer horizons.
TRADING_DAYS = 252
_ANN = TRADING_DAYS ** 0.5

# (dspot_bp, dvol_vp) — one historical session replayed onto the book.
Shock = tuple[float, float]


def daily_returns(closes: list[float]) -> list[float]:
    """Close-to-close simple returns. Non-positive closes break the ratio and are
    skipped (a bad bar must not inject a ±100% scenario)."""
    out: list[float] = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev > 0 and cur > 0:
            out.append(cur / prev - 1.0)
    return out


def rolling_rv_vp(rets: list[float], window: int = RV_WINDOW) -> list[float | None]:
    """Trailing annualised realised vol in **vol points**, aligned to ``rets``.

    ``None`` for the first ``window - 1`` entries (no full window yet)."""
    if window < 2:
        raise ValueError("window must be >= 2")
    out: list[float | None] = []
    for i in range(len(rets)):
        if i + 1 < window:
            out.append(None)
            continue
        w = rets[i + 1 - window : i + 1]
        mean = sum(w) / window
        var = sum((x - mean) ** 2 for x in w) / (window - 1)
        out.append((var ** 0.5) * _ANN * 100.0)
    return out


def market_shocks(closes: list[float], rv_window: int = RV_WINDOW) -> list[Shock]:
    """Daily closes → one ``(dspot_bp, dvol_vp)`` scenario per usable session.

    The first ``rv_window`` sessions are consumed by the realised-vol window, so
    ~1 year of daily bars yields ~230 scenarios."""
    rets = daily_returns(closes)
    rv = rolling_rv_vp(rets, rv_window)
    shocks: list[Shock] = []
    for i in range(1, len(rets)):
        prev_rv, cur_rv = rv[i - 1], rv[i]
        if prev_rv is None or cur_rv is None:
            continue
        shocks.append((rets[i] * 10_000.0, cur_rv - prev_rv))
    return shocks


def simulate_pnl_by_position(
    baselines: list[dict[str, Any]],
    spot: float,
    shocks: list[Shock],
) -> list[list[float]]:
    """Per-position P&L vector across the scenarios (rows = positions).

    ``reval_book`` is additive over the book, so the portfolio vector is the
    column-wise sum — computing per position costs nothing extra and is what the
    component-VaR decomposition needs."""
    return [
        [reval_book([b], spot, dspot_bp=ds, dvol_vp=dv, output="pnl") for ds, dv in shocks]
        for b in baselines
    ]


def portfolio_pnl(by_position: list[list[float]]) -> list[float]:
    """Column-wise sum of the per-position vectors = the book's P&L per scenario."""
    if not by_position:
        return []
    return [sum(col) for col in zip(*by_position, strict=True)]
