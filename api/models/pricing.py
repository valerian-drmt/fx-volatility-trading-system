"""Pydantic models for /price, /greeks, /iv — Black-Scholes inputs / outputs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OptionType = Literal["CALL", "PUT"]


class _BSInput(BaseModel):
    """Common BS fields : forward, strike, maturity, option type."""

    spot: float = Field(gt=0, description="Forward price (F) used in BS formula")
    strike: float = Field(gt=0, description="Option strike")
    maturity_days: int = Field(gt=0, le=3650, description="Days to expiry (capped 10y)")
    option_type: OptionType = "CALL"


class PriceRequest(_BSInput):
    """Ask for a BS price given (F, K, T, sigma, right)."""

    volatility: float = Field(
        gt=0, le=5.0, description="Annualized vol in decimal form (0.075 = 7.5%)"
    )


class GreeksRequest(PriceRequest):
    """Same shape as PriceRequest — alias kept for OpenAPI clarity."""


class ImpliedVolRequest(_BSInput):
    """Invert BS from a market price to get the implied vol."""

    market_price: float = Field(gt=0, description="Observed market price of the option")


class PriceResponse(BaseModel):
    price: float


class GreeksResponse(BaseModel):
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float


class ImpliedVolResponse(BaseModel):
    implied_volatility: float
