"""Pydantic models for /vol-history + /system-stats."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class VolHistoryRow(BaseModel):
    """Lightweight vol surface snapshot — no JSONB payload, just timestamp + F/spot."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    underlying: str
    spot: Decimal
    forward: Decimal | None


class BarRow(BaseModel):
    """One OHLC candle. ``t`` = bar-open epoch milliseconds (UTC)."""

    t: int
    o: float
    h: float
    l: float  # noqa: E741 — compact OHLC wire key, matches the engine/frontend
    c: float


class EngineStats(BaseModel):
    name: str
    state: str   # OK | STALE (Ns) | DOWN
    heartbeat_age_s: float | None


class SystemStats(BaseModel):
    """Portfolio-level stats — counts Postgres + engine heartbeats from Redis."""

    timestamp: datetime
    counts: dict[str, int]   # table_name -> row_count
    engines: list[EngineStats]
