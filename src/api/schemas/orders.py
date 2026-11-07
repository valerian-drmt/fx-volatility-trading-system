"""Typed bodies for the order routes the api forwards to the execution engine
(#300, audit 01#14). Same bounds as the engine's own models, so a bad request
is refused at the edge with a 422 instead of travelling to the engine."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PlaceOrderBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    sec_type: Literal["FUT", "FOP", "CASH"]
    side: Literal["BUY", "SELL"]
    # Contracts for FUT/FOP, base-currency notional for CASH (spot EUR.USD:
    # qty=100_000 = 100k EUR), hence the per-secType bound below.
    qty: int = Field(gt=0)
    limit_price: float | None = Field(default=None, gt=0, lt=1_000_000)
    expiry: str | None = Field(default=None, pattern=r"^\d{8}$")
    strike: float | None = Field(default=None, gt=0, lt=100)
    right: Literal["C", "P"] | None = None
    exchange: str = Field(default="CME", min_length=1, max_length=20)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    trading_class: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _per_sec_type_bounds(self) -> PlaceOrderBody:
        if self.sec_type == "CASH":
            if self.qty > 5_000_000:
                raise ValueError("spot qty must be <= 5,000,000 (base-ccy notional)")
        else:
            if self.qty > 1000:
                raise ValueError("qty must be <= 1000 contracts")
            if self.limit_price is None:
                raise ValueError("limit_price is required for FUT/FOP orders")
        return self


class ClosePositionBody(BaseModel):
    limit_price: float = Field(gt=0, lt=1_000_000)
