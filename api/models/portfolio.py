"""Pydantic models for /positions, /risk, /pnl-curve, /history."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class PositionView(BaseModel):
    """A single position row — mirrors ``persistence.models.Position``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    instrument_type: str
    side: str
    quantity: Decimal
    strike: Decimal | None
    maturity: date | None
    option_type: str | None
    entry_price: Decimal
    entry_timestamp: datetime
    status: str


class PositionSnapshotView(BaseModel):
    """One snapshot row used by /history."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    spot: Decimal | None
    iv: Decimal | None
    delta_usd: Decimal | None
    vega_usd: Decimal | None
    gamma_usd: Decimal | None
    theta_usd: Decimal | None
    pnl_usd: Decimal | None


class GreeksAggregated(BaseModel):
    """Portfolio-level greeks as published by RiskEngine to Redis."""

    timestamp: datetime
    greeks: dict[str, Any]


class PnLCurve(BaseModel):
    """Spot vs PnL curve (~31 points) from RiskEngine."""

    timestamp: datetime
    curve: dict[str, Any]


class HistoryResponse(BaseModel):
    position_id: int
    snapshots: list[PositionSnapshotView]
