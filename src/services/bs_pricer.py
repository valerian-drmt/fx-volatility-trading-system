"""Re-export of :mod:`core.pricing.bs` for backwards compatibility.

R7 PR #1 relocated the Black-Scholes closed-form pricers to ``core/pricing/``
so the three engine containers (plus the FastAPI pricing router) share a
single authoritative implementation. This shim keeps legacy imports
(``from services.bs_pricer import bs_price``) working without touching
every call site at once.
"""
from core.pricing.bs import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_theta,
    bs_vega,
    interpolate_iv,
)

__all__ = [
    "bs_delta",
    "bs_gamma",
    "bs_price",
    "bs_theta",
    "bs_vega",
    "interpolate_iv",
]
