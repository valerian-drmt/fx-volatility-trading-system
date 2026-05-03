"""Slippage and limit-price math for IB order submission.

Pure helpers ; no IB calls, no DB.
"""
from __future__ import annotations


def compute_limit_price(
    preview_price: float, side: str, slippage_tolerance_pct: float = 0.5,
) -> float:
    """Limit price = preview_price ± tolerance%, in BUY/SELL favourable direction.

    BUY  → cap above preview (we accept paying up to preview × (1+tol))
    SELL → floor below preview (we accept receiving down to preview × (1−tol))

    Tolerance default 0.5% per spec §13 decision 7.
    """
    if preview_price <= 0:
        raise ValueError(f"preview_price must be positive, got {preview_price}")
    if slippage_tolerance_pct < 0:
        raise ValueError("slippage_tolerance_pct must be ≥ 0")
    side_u = side.upper()
    factor = slippage_tolerance_pct / 100.0
    if side_u == "BUY":
        return preview_price * (1.0 + factor)
    if side_u == "SELL":
        return preview_price * (1.0 - factor)
    raise ValueError(f"side must be BUY or SELL, got {side}")


def compute_slippage_per_contract(
    preview_price: float, avg_fill_price: float, side: str,
) -> float:
    """Signed slippage per contract — positive = we paid worse than preview.

    BUY  : avg_fill - preview  (positive if we paid more)
    SELL : preview - avg_fill  (positive if we received less)
    """
    side_u = side.upper()
    if side_u == "BUY":
        return avg_fill_price - preview_price
    if side_u == "SELL":
        return preview_price - avg_fill_price
    raise ValueError(f"side must be BUY or SELL, got {side}")
