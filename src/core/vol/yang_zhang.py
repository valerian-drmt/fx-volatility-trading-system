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

    Returns ``None`` when the window is shorter than three rows — the
    Yang-Zhang estimator needs at least two overnight + two open-close
    moves to converge and we prefer a missing value over garbage.
    """
    dw = df_ohlc.tail(window).copy()
    n = len(dw)
    if n < 3:
        return None
    o = np.log(dw["open"].values)
    h = np.log(dw["high"].values)
    lo = np.log(dw["low"].values)
    c = np.log(dw["close"].values)
    overnight = o[1:] - c[:-1]
    oc = c[1:] - o[1:]
    rs = (h[1:] - c[1:]) * (h[1:] - o[1:]) + (lo[1:] - c[1:]) * (lo[1:] - o[1:])
    s2_on = np.var(overnight, ddof=1)
    s2_oc = np.var(oc, ddof=1)
    s2_rs = np.mean(rs)
    k_yz = 0.34 / (1.34 + (n + 1) / (n - 1))
    s2_yz = s2_on + k_yz * s2_oc + (1 - k_yz) * s2_rs
    return float(np.sqrt(max(s2_yz, 0) * 252) * 100)
