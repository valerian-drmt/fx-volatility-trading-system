"""Yang-Zhang realised-volatility estimator (annualised, in percentage points).

Inputs are OHLC bars as a pandas DataFrame with columns ``open`` / ``high``
/ ``low`` / ``close`` and one row per period (daily by convention). The
function is side-effect free and can be called from any thread / process /
container.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def yang_zhang_rv_pct(df_ohlc: Any, window: int) -> float | None:
    """Annualised RV % over the trailing ``window`` rows of ``df_ohlc``.

    Returns ``None`` when fewer than three VALID rows remain (finite,
    positive prices with high >= low) — the estimator needs at least two
    overnight + two open-close moves, and a missing value beats garbage.
    """
    dw = df_ohlc.tail(window)
    try:
        o_raw = np.asarray(dw["open"].values, dtype=float)
        h_raw = np.asarray(dw["high"].values, dtype=float)
        l_raw = np.asarray(dw["low"].values, dtype=float)
        c_raw = np.asarray(dw["close"].values, dtype=float)
    except (KeyError, TypeError, ValueError):
        return None
    # Input validation (#312, audit 02#9): a NaN, a zero or negative price or
    # a high below the low used to turn into NaN / inf through the logs. Such
    # rows are dropped; the estimator needs three clean rows to say anything.
    ok = (
        np.isfinite(o_raw) & np.isfinite(h_raw) & np.isfinite(l_raw) & np.isfinite(c_raw)
        & (o_raw > 0) & (h_raw > 0) & (l_raw > 0) & (c_raw > 0) & (h_raw >= l_raw)
    )
    o, h, lo, c = (np.log(v[ok]) for v in (o_raw, h_raw, l_raw, c_raw))
    n = int(ok.sum())
    if n < 3:
        return None
    overnight = o[1:] - c[:-1]
    oc = c[1:] - o[1:]
    rs = (h[1:] - c[1:]) * (h[1:] - o[1:]) + (lo[1:] - c[1:]) * (lo[1:] - o[1:])
    s2_on = np.var(overnight, ddof=1)
    s2_oc = np.var(oc, ddof=1)
    s2_rs = np.mean(rs)
    # Yang-Zhang's k uses the number of PERIODS (n rows give n-1 overnight /
    # open-close moves), not the number of rows.
    m = n - 1
    k_yz = 0.34 / (1.34 + (m + 1) / (m - 1))
    s2_yz = s2_on + k_yz * s2_oc + (1 - k_yz) * s2_rs
    if not np.isfinite(s2_yz):
        return None
    return float(np.sqrt(max(s2_yz, 0) * 252) * 100)
