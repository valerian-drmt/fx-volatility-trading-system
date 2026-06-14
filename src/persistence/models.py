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
    """Open position book — one row per IB contract held.

    Schema mirrors Portfolio panel section E. The IB ``localSymbol`` is the
    single canonical key (engines parse it via
    ``shared.contracts.parse_local_symbol`` when they need contract specs).
    """

    __tablename__ = "open_position"
    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_positions_side"),
    )

    # Column order here mirrors the physical schema after migration 028 :
    # exactly panel E columns + entry_timestamp + updated_at, nothing else.
    id: Mapped[int] = mapped_column(primary_key=True)
    structure: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    tenor: Mapped[str | None] = mapped_column(String(10))
    expiry: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    nominal_eur: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    contract_price_entry: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    current_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    delta_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gamma_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    vega_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    theta_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    iv: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    vanna_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    volga_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    entry_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
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
    __tablename__ = "open_position_history"

    # Schema mirrors ``positions`` (panel E columns) + position_id + timestamp.
    # risk-engine writes one row per OPEN position per cycle.
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("open_position.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    structure: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    tenor: Mapped[str | None] = mapped_column(String(10))
    expiry: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    nominal_eur: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    contract_price_entry: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    current_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    delta_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gamma_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    vega_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    theta_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    iv: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    vanna_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    volga_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    position: Mapped[Position] = relationship(back_populates="snapshots")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("ib_order_id", name="uq_trades_ib_order_id"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_trades_side"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("open_position.id"))

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
    __tablename__ = "account_history"

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
    __tablename__ = "vol_surface_history"
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


class RegimeSnapshot(Base):
    """Step 1 regime classification snapshot (one row per vol-engine cycle)."""

    __tablename__ = "regime_snapshot_history"
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

    # Feature enrichment (migration 018) — per-feature bucket/Δz-1h/pct/signal.
    # The bucket/signal CHECK constraints are enforced by migration 018.
    bucket_vol_level: Mapped[str | None] = mapped_column(String(4))
    delta_z_1h_vol_level: Mapped[float | None] = mapped_column(Float)
    pct_vol_level: Mapped[int | None] = mapped_column(Integer)
    signal_vol_level: Mapped[str | None] = mapped_column(String(8))
    bucket_vol_of_vol: Mapped[str | None] = mapped_column(String(4))
    delta_z_1h_vol_of_vol: Mapped[float | None] = mapped_column(Float)
    pct_vol_of_vol: Mapped[int | None] = mapped_column(Integer)
    signal_vol_of_vol: Mapped[str | None] = mapped_column(String(8))
    bucket_term_slope: Mapped[str | None] = mapped_column(String(4))
    delta_z_1h_term_slope: Mapped[float | None] = mapped_column(Float)
    pct_term_slope: Mapped[int | None] = mapped_column(Integer)
    signal_term_slope: Mapped[str | None] = mapped_column(String(8))


class RegimeLookup(Base):
    """Joint-pattern → regime mapping (15 base patterns, 5-bucket expansion).

    Pattern shape ``"(<bucket_vol_level>,<bucket_vol_of_vol>,<bucket_term_slope>)"``,
    e.g. ``"(0,0,+)"``. Unmapped tail-extreme combos fall back to a seeded row.
    """

    __tablename__ = "regime_pattern_dict"

    pattern: Mapped[str] = mapped_column(String(20), primary_key=True)
    regime_id: Mapped[int] = mapped_column(Integer, nullable=False)
    regime_name: Mapped[str] = mapped_column(String(60), nullable=False)
    family: Mapped[str] = mapped_column(String(40), nullable=False)
    action_default: Mapped[str] = mapped_column(String(80), nullable=False)
    asymmetry_note: Mapped[str | None] = mapped_column(String(120))
    intensity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Event(Base):
    """Scheduled macro event (manual or feed-sourced), read by the regime gate."""

    __tablename__ = "event_calendar"
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

    __tablename__ = "pca_surface_snapshot_history"
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

    __tablename__ = "pca_model"

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
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pca_model.id"))

    cosine_similarity_pc1: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    cosine_similarity_pc2: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    cosine_similarity_pc3: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    sign_flip_pc1: Mapped[bool | None] = mapped_column(Boolean)
    sign_flip_pc2: Mapped[bool | None] = mapped_column(Boolean)
    sign_flip_pc3: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(String(500))


class PcaSignal(Base):
    """1 row per PC per vol-engine cycle. Feeds Panel 2 + history charts."""

    __tablename__ = "pca_signal_history"
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
        BigInteger, ForeignKey("pca_model.id"), nullable=False,
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

    __tablename__ = "pca_structure_recommendation"
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

    __tablename__ = "trade_preview"
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
    pca_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pca_signal_history.id"))
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

    __tablename__ = "book_state_snapshot_history"

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


class AppConfigScalar(Base):
    """Unified scalar config — folds delta_hedge_config + risk_limits (migration 033).

    One row per tunable, keyed by ``(namespace, name)``. ``namespace='delta_hedge'``
    carries the former delta_hedge_config rows, ``namespace='risk'`` the former
    risk_limits rows. Hot-reloadable, edited via the config endpoints.
    """

    __tablename__ = "config_scalar"
    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_config_scalar_ns_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(40))


# ──────────────────────────────────────────────────────────────────────
# Step 4 — Execution (mock-mode sandbox, cf. STEP4_EXECUTION.md §5)
# ──────────────────────────────────────────────────────────────────────


class TradeStructure(Base):
    """Multi-leg trade : 1 row per Submit (new STEP3/STEP4 workflow)."""

    __tablename__ = "trade_structure"
    __table_args__ = (
        CheckConstraint(
            "state IN ('submitted','partial_fill','fully_filled','partial_fail','fully_failed','closed')",
            name="ck_trade_structures_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    preview_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("trade_preview.preview_id"))
    pca_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pca_signal_history.id"))
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

    __tablename__ = "trade_order"
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
    structure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_structure.id"), nullable=False)
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

    __tablename__ = "trade_fill"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_order.id"), nullable=False)
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

    __tablename__ = "booked_position"
    __table_args__ = (
        CheckConstraint(
            "state IN ('open','closing','closed','expired')",
            name="ck_trade_positions_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    structure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_structure.id"), nullable=False, unique=True)
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
    # IB reconciliation (filled by execution-engine.position_sync each 30 s).
    ib_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ib_qty_total: Mapped[int | None] = mapped_column(Integer)
    ib_qty_diff: Mapped[int | None] = mapped_column(Integer)


class IbConnectionState(Base):
    """Singleton broker connectivity row. UPDATE in place ; never INSERT a new
    row past the migration seed. Heartbeat loop in execution-engine populates."""

    __tablename__ = "runtime_ib_session"
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
    structure_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("trade_structure.id"))
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("trade_order.id"))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(15), nullable=False, default="info")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)


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




class PositionMtmHistory(Base):
    """1 row per monitoring cycle per open position. Series for equity curve
    + P&L attribution + drawdown analysis."""

    __tablename__ = "booked_position_metric_history"
    __table_args__ = (
        UniqueConstraint("position_id", "timestamp", name="uq_mtm_position_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("booked_position.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    iv_avg_legs_pct: Mapped[float | None] = mapped_column(Float)
    current_pnl_gross_usd: Mapped[float] = mapped_column(Float, nullable=False)
    current_pnl_net_usd: Mapped[float] = mapped_column(Float, nullable=False)
    vega_pnl_usd: Mapped[float | None] = mapped_column(Float)
    gamma_pnl_usd: Mapped[float | None] = mapped_column(Float)
    theta_pnl_usd: Mapped[float | None] = mapped_column(Float)
    other_pnl_usd: Mapped[float | None] = mapped_column(Float)
    current_vega_usd_per_volpt: Mapped[float | None] = mapped_column(Float)
    current_gamma_usd_per_pip2: Mapped[float | None] = mapped_column(Float)
    current_theta_usd_per_day: Mapped[float | None] = mapped_column(Float)
    current_delta_unhedged: Mapped[float | None] = mapped_column(Float)


class PositionSignalTracking(Base):
    """Signal-vs-entry comparison snapshot (1 / cycle / position)."""

    __tablename__ = "position_signal_tracking"
    __table_args__ = (
        UniqueConstraint("position_id", "timestamp", name="uq_signal_track_position_ts"),
        CheckConstraint("status IN ('HOLD','TRIM','EXIT')", name="ck_signal_track_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("booked_position.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    triggering_pc: Mapped[int] = mapped_column(Integer, nullable=False)
    current_z_score: Mapped[float] = mapped_column(Float, nullable=False)
    current_label: Mapped[str] = mapped_column(String(15), nullable=False)
    entry_z_score: Mapped[float] = mapped_column(Float, nullable=False)
    entry_label: Mapped[str] = mapped_column(String(15), nullable=False)
    weakening_ratio: Mapped[float | None] = mapped_column(Float)
    sign_flipped: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)


class HedgeOrder(Base):
    """Delta-rebalancing future order on an open position."""

    __tablename__ = "hedge_order"
    __table_args__ = (
        CheckConstraint("side IN ('BUY','SELL')", name="ck_hedge_orders_side"),
        CheckConstraint(
            "state IN ('pending','submitted','filled','failed')",
            name="ck_hedge_orders_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("booked_position.id"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delta_imbalance_at_trigger: Mapped[float] = mapped_column(Float, nullable=False)
    rebalance_threshold_used: Mapped[float] = mapped_column(Float, nullable=False)
    hedge_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    ib_order_id: Mapped[str | None] = mapped_column(String(40))
    fill_price: Mapped[float | None] = mapped_column(Float)
    commission_usd: Mapped[float | None] = mapped_column(Float)
    spread_paid_usd: Mapped[float | None] = mapped_column(Float)
    total_cost_usd: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(15), nullable=False)


class ExitAlert(Base):
    """1 row per exit-rule trigger. Acted on or not."""

    __tablename__ = "exit_alert"
    __table_args__ = (
        CheckConstraint(
            "action_recommended IN ('EXIT','TRIM','ALERT_ONLY')",
            name="ck_exit_alerts_action",
        ),
        CheckConstraint(
            "execution_status IS NULL OR execution_status IN "
            "('in_progress','done','failed','overridden')",
            name="ck_exit_alerts_exec_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("booked_position.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    rule_triggered: Mapped[str] = mapped_column(String(40), nullable=False)
    action_recommended: Mapped[str] = mapped_column(String(15), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_detail: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    auto_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    execution_status: Mapped[str | None] = mapped_column(String(20))
    closing_structure_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trade_structure.id")
    )
    notes: Mapped[str | None] = mapped_column(String(500))


class ExitRulesConfig(Base):
    """Hot-reloadable exit rule params."""

    __tablename__ = "config_exit_rules"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 10", name="ck_exit_rules_priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_name: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_by: Mapped[str | None] = mapped_column(String(40))


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

    __tablename__ = "config_vol_engine"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(String(500))


