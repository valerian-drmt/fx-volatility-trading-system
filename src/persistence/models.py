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
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
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

    # Globaux (en currency de base du compte — typiquement EUR)
    net_liq_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cash_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    unrealized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    accrued_cash: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gross_position_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    # Margin / liquidity (IB account summary tags)
    init_margin_req: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    maint_margin_req: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    excess_liquidity: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    cushion: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))

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
    sigma_fair: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    ecart: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(15), nullable=False)

    rv: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    sigma_fair_p: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    vrp_vol_pts: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))


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
    """Step 1 regime classification snapshot (one row per vol-engine cycle)."""

    __tablename__ = "regime_snapshots"
    __table_args__ = (
        CheckConstraint(
            "label IN ('calm','stressed','pre_event')",
            name="ck_regime_snapshots_label",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, server_default="EURUSD")
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

    event_dampener: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    days_to_next_event: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    next_event_type: Mapped[str | None] = mapped_column(String(40))


class Event(Base):
    """Scheduled macro event (manual or feed-sourced), read by the regime gate."""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("impact IN ('high','medium','low')", name="ck_events_impact"),
        UniqueConstraint("event_hash", name="uq_events_event_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    impact: Mapped[str] = mapped_column(String(10), nullable=False)
    region: Mapped[str] = mapped_column(String(10), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(40), nullable=False, server_default="manual")
    source_url: Mapped[str | None] = mapped_column(String(500))
    event_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
    """1 row per PC per vol-engine cycle. Feeds Panel 2 + history charts."""

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
    """Lookup PC × CHEAP/EXPENSIVE → recommended structure (6-row seed)."""

    __tablename__ = "signal_recommendations_map"
    __table_args__ = (
        UniqueConstraint(
            "pc_id", "signal_label", "is_active",
            name="uq_signal_rec_map_pc_label_active",
        ),
        CheckConstraint(
            "signal_label IN ('CHEAP','EXPENSIVE')", name="ck_signal_rec_map_label",
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


class StructureDefinition(Base):
    """Catalogue des structures supportées (6 rows seed)."""

    __tablename__ = "structure_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    structure_type: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    leg_template: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False)
    min_legs: Mapped[int] = mapped_column(Integer, nullable=False)
    max_legs: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_delta_hedge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    typical_vega_sign: Mapped[str] = mapped_column(String(10), nullable=False)
    typical_gamma_sign: Mapped[str] = mapped_column(String(10), nullable=False)
    typical_theta_sign: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    rationale_for_pc: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TradePreviewRow(Base):
    """Audit log : 1 row par Arm trade."""

    __tablename__ = "trade_previews"
    __table_args__ = (
        CheckConstraint(
            "state IN ('valid_for_submit','blocked','expired','submitted','cancelled')",
            name="ck_trade_previews_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    preview_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pca_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pca_signals.id"))
    triggering_pc: Mapped[int | None] = mapped_column(Integer)
    armed_z_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    armed_signal_label: Mapped[str | None] = mapped_column(String(15))
    structure_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_tenor: Mapped[str] = mapped_column(String(10), nullable=False)
    structure_full_payload: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    state: Mapped[str] = mapped_column(String(25), nullable=False)
    pre_submit_checks: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False)
    blocking_reasons: Mapped[list | None] = mapped_column(JSONB_PORTABLE)
    user_action: Mapped[str | None] = mapped_column(String(20))
    user_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_trade_id: Mapped[int | None] = mapped_column(BigInteger)


class BookStateSnapshot(Base):
    """État aggregé du book (1 row is_current=true par symbol + history)."""

    __tablename__ = "book_state_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="EURUSD")
    total_vega_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_gamma_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_theta_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vega_by_tenor: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)
    vega_by_pc_source: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)
    n_open_structures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_open_legs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notional_engaged_usd: Mapped[float | None] = mapped_column(Float)
    capital_total_usd: Mapped[float | None] = mapped_column(Float)
    margin_used_usd: Mapped[float | None] = mapped_column(Float)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RiskLimit(Base):
    """Hot-reloadable risk parameters (cf. STEP3 §5.5)."""

    __tablename__ = "risk_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    limit_name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    limit_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(40))


# ──────────────────────────────────────────────────────────────────────
# Step 4 — Execution (mock-mode sandbox, cf. STEP4_EXECUTION.md §5)
# ──────────────────────────────────────────────────────────────────────


class TradeStructure(Base):
    """Multi-leg trade : 1 row per Submit (new STEP3/STEP4 workflow)."""

    __tablename__ = "trade_structures"
    __table_args__ = (
        CheckConstraint(
            "state IN ('submitted','partial_fill','fully_filled','partial_fail','fully_failed','closed')",
            name="ck_trade_structures_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    preview_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("trade_previews.preview_id"))
    pca_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pca_signals.id"))
    triggering_pc: Mapped[int | None] = mapped_column(Integer)
    armed_z_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    armed_signal_label: Mapped[str | None] = mapped_column(String(15))
    structure_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_tenor: Mapped[str] = mapped_column(String(10), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    base_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(25), nullable=False)
    state_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ib_combo_order_id: Mapped[str | None] = mapped_column(String(40))
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="mock")
    total_premium_paid_usd: Mapped[float | None] = mapped_column(Float)
    total_slippage_usd: Mapped[float | None] = mapped_column(Float)
    total_commission_usd: Mapped[float | None] = mapped_column(Float)
    total_entry_cost_usd: Mapped[float | None] = mapped_column(Float)
    first_fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fully_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(80))


class StructureOrder(Base):
    """One leg of a multi-leg trade structure."""

    __tablename__ = "structure_orders"
    __table_args__ = (
        UniqueConstraint(
            "structure_id", "leg_idx", "order_role",
            name="uq_structure_orders_structure_leg_role",
        ),
        CheckConstraint(
            "state IN ('pending','submitted','acknowledged','partially_filled','filled','rejected','cancelled','expired')",
            name="ck_structure_orders_state",
        ),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_structure_orders_side"),
        CheckConstraint(
            "order_role IN ('entry','closing','unwind','hedge')",
            name="ck_structure_orders_order_role",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    structure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_structures.id"), nullable=False)
    leg_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    order_role: Mapped[str] = mapped_column(String(20), nullable=False, default="entry")
    ib_order_id: Mapped[str | None] = mapped_column(String(40))
    ib_perm_id: Mapped[str | None] = mapped_column(String(40))
    contract_symbol: Mapped[str] = mapped_column(String(10), nullable=False, default="EUR")
    contract_type: Mapped[str] = mapped_column(String(10), nullable=False)
    contract_expiry: Mapped[date | None] = mapped_column(Date)
    contract_strike: Mapped[float | None] = mapped_column(Float)
    contract_exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="CME")
    contract_currency: Mapped[str] = mapped_column(String(5), nullable=False, default="USD")
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False, default="LMT")
    limit_price: Mapped[float | None] = mapped_column(Float)
    time_in_force: Mapped[str] = mapped_column(String(5), nullable=False, default="DAY")
    preview_iv_pct: Mapped[float | None] = mapped_column(Float)
    preview_price: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(25), nullable=False)
    state_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_code: Mapped[str | None] = mapped_column(String(20))
    rejection_text: Mapped[str | None] = mapped_column(String(300))
    qty_filled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float)
    total_commission_usd: Mapped[float | None] = mapped_column(Float, default=0.0)
    fully_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slippage_per_contract: Mapped[float | None] = mapped_column(Float)
    total_slippage_usd: Mapped[float | None] = mapped_column(Float)


class StructureFill(Base):
    """One execution event on a leg."""

    __tablename__ = "structure_fills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("structure_orders.id"), nullable=False)
    ib_execution_id: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qty_filled: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[float] = mapped_column(Float, nullable=False)
    commission_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    exchange: Mapped[str | None] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    spot_at_fill: Mapped[float | None] = mapped_column(Float)
    bid_at_fill: Mapped[float | None] = mapped_column(Float)
    ask_at_fill: Mapped[float | None] = mapped_column(Float)
    iv_implied_from_fill: Mapped[float | None] = mapped_column(Float)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TradePosition(Base):
    """Position created when a structure is fully_filled. Consumed by Step 5."""

    __tablename__ = "trade_positions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('open','closing','closed','expired')",
            name="ck_trade_positions_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    structure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_structures.id"), nullable=False, unique=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_premium_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entry_total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str] = mapped_column(String(15), nullable=False, default="open")
    state_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    entry_vega_usd_per_volpt: Mapped[float | None] = mapped_column(Float)
    entry_gamma_usd_per_pip2: Mapped[float | None] = mapped_column(Float)
    entry_theta_usd_per_day: Mapped[float | None] = mapped_column(Float)
    entry_spot: Mapped[float | None] = mapped_column(Float)
    entry_iv_avg: Mapped[float | None] = mapped_column(Float)
    entry_regime: Mapped[str | None] = mapped_column(String(20))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(80))
    exit_premium_usd: Mapped[float | None] = mapped_column(Float)
    exit_total_cost_usd: Mapped[float | None] = mapped_column(Float)
    gross_pnl_usd: Mapped[float | None] = mapped_column(Float)
    net_pnl_usd: Mapped[float | None] = mapped_column(Float)


class IbConnectionState(Base):
    """Singleton broker connectivity row. UPDATE in place ; never INSERT a new
    row past the migration seed. Heartbeat loop in execution-engine populates."""

    __tablename__ = "ib_connection_state"
    __table_args__ = (
        CheckConstraint(
            "account_type IS NULL OR account_type IN ('paper','live')",
            name="ck_ib_connection_account_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, default="IB")
    is_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    account_id: Mapped[str | None] = mapped_column(String(40))
    account_type: Mapped[str | None] = mapped_column(String(20))
    available_funds_usd: Mapped[float | None] = mapped_column(Float)
    buying_power_usd: Mapped[float | None] = mapped_column(Float)
    margin_used_usd: Mapped[float | None] = mapped_column(Float)
    gateway_version: Mapped[str | None] = mapped_column(String(40))
    api_version: Mapped[str | None] = mapped_column(String(40))
    last_disconnect_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_disconnects_24h: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )


# ──────────────────────────────────────────────────────────────────────
# Step 5 — Active Positions monitoring (cf. STEP5_ACTIVE_POSITIONS.md §7)
# ──────────────────────────────────────────────────────────────────────

class ExecutionAuditLog(Base):
    """Granular event log for execution debugging / post-mortem."""

    __tablename__ = "execution_audit_log"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('debug','info','warning','error','critical')",
            name="ck_audit_severity",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    structure_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("trade_structures.id"))
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("structure_orders.id"))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(15), nullable=False, default="info")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)
