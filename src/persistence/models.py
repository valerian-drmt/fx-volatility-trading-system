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
    BigInteger,
    Boolean,
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


class Order(Base):
    """IB order lifecycle row. 1 row par order envoyé à IB, status évolue
    selon la lifecycle (PendingSubmit → Submitted → Filled / Cancelled).
    """
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("ib_perm_id", name="uq_orders_ib_perm_id"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_orders_side"),
        CheckConstraint(
            "sec_type IN ('FUT', 'FOP', 'STK', 'OPT', 'CONTFUT')",
            name="ck_orders_sec_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ib_perm_id: Mapped[int | None] = mapped_column(BigInteger)
    ib_order_id: Mapped[int] = mapped_column(Integer, nullable=False)

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    sec_type: Mapped[str] = mapped_column(String(10), nullable=False)
    expiry: Mapped[str | None] = mapped_column(String(10))
    strike: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    right: Mapped[str | None] = mapped_column(String(2))

    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))

    status: Mapped[str] = mapped_column(String(30), nullable=False)
    filled_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False, default=Decimal("0"))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OrderEvent(Base):
    """Audit log : 1 row par action utilisateur envoyée à IB.

    Append-only. Permet de retrouver qui a demandé quoi, quand, et la
    réponse IB exacte (success/failure, message d'erreur).
    """
    __tablename__ = "order_events"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('SUBMIT', 'CANCEL', 'CLOSE_POSITION')",
            name="ck_order_events_action_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    response_payload: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccountSnap(Base):
    __tablename__ = "account_snaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Globaux (en currency de base du compte — typiquement EUR)
    net_liq_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cash_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    unrealized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    accrued_cash: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gross_position_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    # Margin / liquidity
    init_margin_req: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    maint_margin_req: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    excess_liquidity: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cushion: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))

    # Per-currency breakdown (USD / EUR / etc., sans BASE qui est redondant)
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
    sigma_fair: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)  # Q-measure
    ecart: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(15), nullable=False)

    rv: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    sigma_fair_p: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))     # P-measure (HAR/GARCH)
    vrp_vol_pts: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))      # Q − P spread


class SviParam(Base):
    """Per-tenor SVI fit parameters (Phase P2.1). One row per vol cycle per tenor."""

    __tablename__ = "svi_params"
    __table_args__ = (
        UniqueConstraint(
            "timestamp", "underlying", "tenor",
            name="uq_svi_params_ts_underlying_tenor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    underlying: Mapped[str] = mapped_column(String(20), nullable=False)
    tenor: Mapped[str] = mapped_column(String(5), nullable=False)
    a: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    b: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    rho: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    m: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    sigma: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    rmse_fit: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    butterfly_g_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))


class SsviParam(Base):
    """Surface-level SSVI fit parameters (Phase P2.2). One row per vol cycle."""

    __tablename__ = "ssvi_params"
    __table_args__ = (
        UniqueConstraint(
            "timestamp", "underlying", name="uq_ssvi_params_ts_underlying",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    underlying: Mapped[str] = mapped_column(String(20), nullable=False)
    spot: Mapped[Decimal] = mapped_column(Numeric(15, 8), nullable=False)
    eta: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    gamma: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    rho: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    rmse_fit: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    calendar_arb_free: Mapped[bool | None] = mapped_column(Boolean)


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


class VolConfig(Base):
    """Append-only versioned config for the vol trading pipeline.

    Each PUT on ``/api/v1/admin/config`` inserts a new row with the
    next ``version``. The latest row is the source of truth ; older rows
    provide the audit trail + revert + backtest reproducibility
    (a backtest can pin ``config_version=N`` to replay that config).

    ``config`` holds the full :class:`core.config.VolTradingConfig`
    serialized as JSONB -- schema-less on the DB side so adding a new
    Pydantic field does NOT require an Alembic migration.
    """

    __tablename__ = "vol_config"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(String(500))
