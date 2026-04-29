"""Pydantic models for /signals, /vol-history, /backtest, /system-stats."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SignalRow(BaseModel):
    """One row of the signals table — one scan's CHEAP/FAIR/EXPENSIVE verdict."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    underlying: str
    tenor: str
    dte: int
    sigma_mid: Decimal
    sigma_fair: Decimal
    ecart: Decimal
    signal_type: str
    rv: Decimal | None


class VolHistoryRow(BaseModel):
    """Lightweight vol surface snapshot — no JSONB payload, just timestamp + F/spot."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    underlying: str
    spot: Decimal
    forward: Decimal | None


class BacktestRunRow(BaseModel):
    """One row of backtest_runs — headline metrics only."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_name: str
    start_date: date
    end_date: date
    sharpe_ratio: Decimal | None
    max_drawdown_pct: Decimal | None
    total_return_pct: Decimal | None
    n_trades: int | None
    created_at: datetime


class EngineStats(BaseModel):
    name: str
    state: str   # OK | STALE (Ns) | DOWN
    heartbeat_age_s: float | None


class SystemStats(BaseModel):
    """Portfolio-level stats — counts Postgres + engine heartbeats from Redis."""

    timestamp: datetime
    counts: dict[str, int]   # table_name -> row_count
    engines: list[EngineStats]
