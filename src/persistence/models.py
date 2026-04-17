"""SQLAlchemy ORM models for every persistence table.

Tables covered:
    Core trading (R1 PR #3):
        - positions
        - position_snapshots
        - trades
        - account_snaps

    Vol and analytics (R1 PR #4):
        - vol_surfaces
        - signals
        - backtest_runs

All models share the same declarative Base so Alembic can diff them
together in R1 PR #5.

Reference: releases/architecture_finale_project/08-postgresql.md
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONB_PORTABLE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base shared by every persistence model."""


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint(
            "instrument_type IN ('SPOT', 'FUTURE', 'OPTION')",
            name="ck_positions_instrument_type",
        ),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_positions_side"),
        CheckConstraint(
            "option_type IS NULL OR option_type IN ('CALL', 'PUT')",
            name="ck_positions_option_type",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'CLOSED', 'EXPIRED')",
            name="ck_positions_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)

    strike: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    maturity: Mapped[date | None] = mapped_column(Date)
    option_type: Mapped[str | None] = mapped_column(String(4))

    entry_price: Mapped[Decimal] = mapped_column(Numeric(15, 8), nullable=False)
    entry_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    exit_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    snapshots: Mapped[list[PositionSnapshot]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )
    trades: Mapped[list[Trade]] = relationship(back_populates="position")


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    spot: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    iv: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))

    delta_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    vega_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gamma_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    theta_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    position: Mapped[Position] = relationship(back_populates="snapshots")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("ib_order_id", name="uq_trades_ib_order_id"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_trades_side"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))

    ib_order_id: Mapped[str | None] = mapped_column(String(50))

    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(15, 8), nullable=False)
    commission: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    spot_at_execution: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    iv_at_execution: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))

    position: Mapped[Position | None] = relationship(back_populates="trades")


class AccountSnap(Base):
    __tablename__ = "account_snaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    net_liq_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cash_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    buying_power_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    available_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    unrealized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    realized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gross_position_value_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    currencies: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)

    open_positions_count: Mapped[int | None] = mapped_column(Integer)


class VolSurface(Base):
    __tablename__ = "vol_surfaces"
    __table_args__ = (
        UniqueConstraint("timestamp", "underlying", name="uq_vol_surfaces_ts_underlying"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    underlying: Mapped[str] = mapped_column(String(20), nullable=False)
    spot: Mapped[Decimal] = mapped_column(Numeric(15, 8), nullable=False)
    forward: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))

    surface_data: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    fair_vol_data: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)
    rv_data: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)

    scan_duration_s: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint(
            "timestamp", "underlying", "tenor", name="uq_signals_ts_underlying_tenor"
        ),
        CheckConstraint(
            "signal_type IN ('CHEAP', 'EXPENSIVE', 'FAIR')",
            name="ck_signals_signal_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    underlying: Mapped[str] = mapped_column(String(20), nullable=False)
    tenor: Mapped[str] = mapped_column(String(5), nullable=False)
    dte: Mapped[int] = mapped_column(Integer, nullable=False)

    sigma_mid: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    sigma_fair: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    ecart: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(15), nullable=False)

    rv: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    sortino_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    max_drawdown_duration_days: Mapped[int | None] = mapped_column(Integer)
    hit_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    total_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    annualized_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    annualized_vol_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    n_trades: Mapped[int | None] = mapped_column(Integer)
    avg_holding_period_days: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))

    equity_curve: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)
    trades_log: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
