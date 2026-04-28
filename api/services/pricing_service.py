"""Wrap bs_pricer.* — converts API request models to BS primitives + IV root-find."""
from __future__ import annotations

from scipy.optimize import brentq

from api.models.pricing import (
    GreeksRequest,
    GreeksResponse,
    ImpliedVolRequest,
    ImpliedVolResponse,
    PriceRequest,
    PriceResponse,
)
from services import bs_pricer

# Convert incoming "CALL"/"PUT" API tokens to bs_pricer's "C"/"P" convention.
_RIGHT_MAP: dict[str, str] = {"CALL": "C", "PUT": "P"}


def _T_years(maturity_days: int) -> float:
    """Actuarial-style annualization (365 days, consistent with bs_pricer.theta)."""
    return maturity_days / 365.0


def compute_price(req: PriceRequest) -> PriceResponse:
    right = _RIGHT_MAP[req.option_type]
    price = bs_pricer.bs_price(req.spot, req.strike, _T_years(req.maturity_days),
                                req.volatility, right)
    return PriceResponse(price=price)


def compute_greeks(req: GreeksRequest) -> GreeksResponse:
    """Return price + 4 greeks in a single payload (UI dashboards expect them bundled)."""
    F, K, T = req.spot, req.strike, _T_years(req.maturity_days)
    sigma, right = req.volatility, _RIGHT_MAP[req.option_type]
    return GreeksResponse(
        price=bs_pricer.bs_price(F, K, T, sigma, right),
        delta=bs_pricer.bs_delta(F, K, T, sigma, right),
        gamma=bs_pricer.bs_gamma(F, K, T, sigma),
        vega=bs_pricer.bs_vega(F, K, T, sigma),
        theta=bs_pricer.bs_theta(F, K, T, sigma, right),
    )


def compute_implied_vol(req: ImpliedVolRequest) -> ImpliedVolResponse:
    """Invert BS via Brent's method — raises ValueError if market_price is unreachable.

    Range [1e-6, 5.0] = 0.0001%% to 500% annualized vol. Anything outside
    this bracket is implausible and the caller should catch the ValueError
    to return 422 Unprocessable Entity.
    """
    F, K, T = req.spot, req.strike, _T_years(req.maturity_days)
    right = _RIGHT_MAP[req.option_type]

    def diff(sigma: float) -> float:
        return bs_pricer.bs_price(F, K, T, sigma, right) - req.market_price

    iv = brentq(diff, 1e-6, 5.0, xtol=1e-8)
    return ImpliedVolResponse(implied_volatility=iv)
