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


class RegimeSnapshot(Base):
    """One row per vol-engine cycle — Panel 1 audit + stability gate input."""

    __tablename__ = "regime_snapshots"
    __table_args__ = (
        CheckConstraint(
            "label IN ('calm','stressed','pre_event')",
            name="ck_regime_snapshots_label",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="EURUSD")
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str] = mapped_column(String(40), nullable=False)

    vol_level_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    vol_of_vol_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    term_slope_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    vol_level_z: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    vol_of_vol_z: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    term_slope_z: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    p_calm: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    p_stressed: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    p_pre_event: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    event_dampener: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    days_to_next_event: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    next_event_type: Mapped[str | None] = mapped_column(String(40))


class FeatureHistory(Base):
    """Wide-format timeseries of features — feeds rolling z-scores & vol_of_vol."""

    __tablename__ = "feature_history"
    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", name="uq_feature_history_symbol_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="EURUSD")
    iv_atm_1m_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    iv_atm_3m_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    iv_atm_6m_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    rv_yz_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    vol_of_vol_30d_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    term_slope_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    vol_level_z90: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    vol_of_vol_z90: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    term_slope_z90: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))


class Event(Base):
    """Economic calendar — drives event_dampener flag + Panel 1 zone 4.

    ``event_hash`` is the stable identity for inter-cycle / inter-source dedup.
    It is computed by ``api.orchestration.events.hashing.event_hash`` and enforced
    UNIQUE at the DB level (cf. migration 012).
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("impact IN ('high','medium','low')", name="ck_events_impact"),
        UniqueConstraint("event_hash", name="uq_events_event_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_hash: Mapped[str | None] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    impact: Mapped[str] = mapped_column(String(10), nullable=False)
    region: Mapped[str] = mapped_column(String(10), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    source_url: Mapped[str | None] = mapped_column(String(500))
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VrpTableDefault(Base):
    """Hardcoded VRP placeholder by (regime, tenor) — 18 rows seeded by migration 010."""

    __tablename__ = "vrp_table_default"
    __table_args__ = (
        UniqueConstraint("regime", "tenor", name="uq_vrp_table_default_regime_tenor"),
        CheckConstraint(
            "regime IN ('calm','stressed','pre_event')",
            name="ck_vrp_table_default_regime",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    regime: Mapped[str] = mapped_column(String(20), nullable=False)
    tenor: Mapped[str] = mapped_column(String(5), nullable=False)
    vrp_vol_pts: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    calibration_method: Mapped[str] = mapped_column(
        String(40), nullable=False, default="hardcoded_placeholder"
    )
    calibration_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(String(500))


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


# ──────────────────────────────────────────────────────────────────────
# Step 2 — PCA factor model (cf. STEP2_SIGNAL_DETECTION.md §5)
# ──────────────────────────────────────────────────────────────────────

_TENORS = ("1m", "2m", "3m", "4m", "5m", "6m")
_DELTAS = ("10dp", "25dp", "atm", "25dc", "10dc")


class SurfaceSnapshotHourly(Base):
    """30-dim hourly snapshot for PCA fit (6 tenors × 5 deltas)."""

    __tablename__ = "surface_snapshots_hourly"
    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", name="uq_surface_snap_hourly_symbol_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="EURUSD")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="live_engine")
    spot_at_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    n_strikes_present: Mapped[int | None] = mapped_column(Integer)
    has_no_arb_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 30 IV columns iv_{tenor}_{delta} declared dynamically below.


for _t in _TENORS:
    for _d in _DELTAS:
        setattr(
            SurfaceSnapshotHourly, f"iv_{_t}_{_d}",
            mapped_column(Numeric(10, 6), nullable=True),
        )


class PcaModel(Base):
    """Versioned PCA model — JSONB means/stds/loadings (schema-less for dim flex)."""

    __tablename__ = "pca_models"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    fit_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    fit_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fit_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    n_obs_used: Mapped[int] = mapped_column(Integer, nullable=False)

    means: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False)
    stds: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False)
    loadings: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False)
    eigenvalues: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False)
    variance_explained_ratio: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False)

    n_components_kept: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pca_models.id"))

    cosine_similarity_pc1: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    cosine_similarity_pc2: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    cosine_similarity_pc3: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    sign_flip_pc1: Mapped[bool | None] = mapped_column(Boolean)
    sign_flip_pc2: Mapped[bool | None] = mapped_column(Boolean)
    sign_flip_pc3: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(String(500))


class PcaSignal(Base):
    """1 row per PC per vol-engine cycle. Feed Panel 2 + history charts."""

    __tablename__ = "pca_signals"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "timestamp", "pca_model_id", "pc_id",
            name="uq_pca_signals_symbol_ts_model_pc",
        ),
        CheckConstraint(
            "label IN ('CHEAP','FAIR','EXPENSIVE')", name="ck_pca_signals_label",
        ),
        CheckConstraint("pc_id > 0", name="ck_pca_signals_pc_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="EURUSD")
    pca_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pca_models.id"), nullable=False,
    )
    pc_id: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(15, 8), nullable=False)
    z_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    label: Mapped[str] = mapped_column(String(15), nullable=False)
    actionable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actionable_reason: Mapped[str | None] = mapped_column(String(80))
    sub_signals: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)
    recommended_structure: Mapped[str | None] = mapped_column(String(80))


class SignalRecommendationsMap(Base):
    """Lookup PC × CHEAP/EXPENSIVE → structure recommandée (6 rows seed)."""

    __tablename__ = "signal_recommendations_map"
    __table_args__ = (
        UniqueConstraint(
            "pc_id", "signal_label", "is_active",
            name="uq_signal_rec_map_pc_label_active",
        ),
        CheckConstraint(
            "signal_label IN ('CHEAP','EXPENSIVE')",
            name="ck_signal_rec_map_label",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pc_id: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_label: Mapped[str] = mapped_column(String(15), nullable=False)
    recommended_structure: Mapped[str] = mapped_column(String(60), nullable=False)
    default_tenor: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    rationale: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
