"""Pydantic models for /positions, /risk, /pnl-curve, /history."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class PositionView(BaseModel):
    """A single position row — mirrors ``persistence.models.Position`` after
    migration 028. Field order = panel E + entry_timestamp + updated_at.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    structure: str
    side: str
    tenor: str | None
    expiry: date | None
    quantity: Decimal
    nominal_eur: Decimal | None
    contract_price_entry: Decimal | None
    market_price: Decimal | None
    current_pnl_usd: Decimal | None
    delta_usd: Decimal | None
    gamma_usd: Decimal | None
    vega_usd: Decimal | None
    theta_usd: Decimal | None
    iv: Decimal | None
    vanna_usd: Decimal | None
    volga_usd: Decimal | None
    entry_timestamp: datetime
    updated_at: datetime


class PositionSnapshotView(BaseModel):
    """One snapshot row used by /history. Mirrors the panel-E shape after
    migration 030."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    structure: str
    side: str
    tenor: str | None
    expiry: date | None
    quantity: Decimal
    nominal_eur: Decimal | None
    contract_price_entry: Decimal | None
    market_price: Decimal | None
    current_pnl_usd: Decimal | None
    delta_usd: Decimal | None
    gamma_usd: Decimal | None
    vega_usd: Decimal | None
    theta_usd: Decimal | None
    iv: Decimal | None
    vanna_usd: Decimal | None
    volga_usd: Decimal | None


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
