"""SQLAlchemy ORM models for every persistence table.

All models share the same declarative Base so Alembic can diff them
together. Live ER diagram + drift detection (ORM vs DB) lives in the
dev console (``/dev`` → 🗺 DB Schema tab).
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONB_PORTABLE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base shared by every persistence model."""


class OpenPosition(Base):
    """Open position book — one row per IB contract held.

    Schema mirrors Portfolio panel section E. The IB ``localSymbol`` is the
    single canonical key (engines parse it via
    ``shared.contracts.parse_local_symbol`` when they need contract specs).
    """

    __tablename__ = "open_position"  # renamed in migration 033 (was 'position')
    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_positions_side"),
    )

    # Column order here mirrors the physical schema after migration 028 :
    # exactly panel E columns + entry_timestamp + updated_at, nothing else.
    id: Mapped[int] = mapped_column(primary_key=True)
    structure: Mapped[str] = mapped_column(String(20), nullable=False)
    # Migration 032 : user-friendly twin of ``structure``. One of the
    # 8 labels in ``core.products.PRODUCT_LABELS``. Nullable for now ;
    # backfilled by 032 and computed by writers going forward. Promoted
    # to NOT NULL in a follow-up migration once writer coverage is proven.
    product_label: Mapped[str | None] = mapped_column(String(40))
    # Migration 034 : Murex-aligned identity stack.
    #   contract_id = IB ``conId`` (atomic instrument id).
    #   trade_id    = FK to ``trade_structure.id`` (the strategy / structure
    #                 grouping the contracts ; 2 legs of a straddle share
    #                 one trade_id).
    #   package_id  = FK to ``package.id`` (denormalised from
    #                 trade_structure.package_id ; lets the UI sort /
    #                 filter without a JOIN).
    # All 3 are nullable : a position came from a direct IB order
    # outside the booking pipeline has trade_id=NULL (and therefore
    # package_id=NULL too).
    contract_id: Mapped[int | None] = mapped_column(BigInteger)
    trade_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trade_structure.id", ondelete="SET NULL"),
    )
    package_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("package.id", ondelete="SET NULL"),
    )
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
    # Migration 033 : renamed from ``updated_at``. Aligns with the
    # ``open_position_history.timestamp`` column so both tables expose
    # the same temporal anchor.
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    metric_history: Mapped[list[OpenPositionHistory]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )
    # Note: `trades` relationship removed — Trade ORM dropped in
    # migration 025 (legacy fills journal had no live writers).


class OpenPositionHistory(Base):
    """Greeks + pnl + iv time series per IB-leg position (renamed from
    PositionSnapshot in migration 026 Theme 2). Sole writer = risk-engine.
    One row per OPEN position per 2s cycle."""

    __tablename__ = "open_position_history"  # renamed in migration 033

    # Schema mirrors ``open_position`` 1-to-1 plus position_id + timestamp.
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("open_position.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    structure: Mapped[str] = mapped_column(String(20), nullable=False)
    # Mirror of OpenPosition.product_label (migration 032). See OpenPosition class.
    product_label: Mapped[str | None] = mapped_column(String(40))
    # Mirror of OpenPosition.contract_id / trade_id / package_id (migration 034).
    contract_id: Mapped[int | None] = mapped_column(BigInteger)
    trade_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trade_structure.id", ondelete="SET NULL"),
    )
    package_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("package.id", ondelete="SET NULL"),
    )
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

    position: Mapped[OpenPosition] = relationship(back_populates="metric_history")


# Trade, Order, OrderEvent ORM classes deleted in migration 025 — all
# three were dead code (zero writers, no readers). Their roles are now
# covered by :
#   - trade_fill (formerly structure_fills) — fills journal
#   - trade_order (formerly structure_orders) — active IB orders
#   - trade_event (event_type='audit') — order action audit log


class AccountHistory(Base):
    """Renamed from AccountSnap in migration 026 (Theme 2). Snapshot time
    series of the IB broker account (NetLiq, cash, margin, cushion).
    Writer = execution-engine every 30s."""

    __tablename__ = "account_history"  # renamed in migration 026

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
    __tablename__ = "vol_surface_history"  # renamed in migration 023
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
    """One row per vol-engine cycle — Panel 1 audit + stability gate input."""

    __tablename__ = "regime_snapshot_history"  # renamed 023 → 040 (_history suffix alignment)
    __table_args__ = (
        CheckConstraint(
            "label IN ('calm','stressed','pre_event')",
            name="ck_regime_snapshot_history_label",
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

    # ── Step 2 features-enrichment columns (migration 018, populated by E3) ──
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


# ``regime_pattern_dict`` (ORM ``RegimeLookup``) was dropped in
# migration 039. Joint-pattern → regime mapping now lives in
# ``core.regime_patterns.REGIME_PATTERNS`` — single source of truth.


class FeatureHistory(Base):
    """Wide-format timeseries of features — feeds rolling z-scores & vol_of_vol."""

    __tablename__ = "feature_history"  # renamed in migration 023
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

    __tablename__ = "event_calendar"  # renamed in migration 023
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


# ``vrp_default_curve`` table (ORM ``VrpTableDefault``) was dropped in
# migration 038. Its 18 placeholder rows (one per regime × tenor) were
# bit-for-bit identical to ``core.vol.vrp.VRP_DEFAULTS_VOL_PTS`` (the
# alembic 010 seed populated the table from that dict) and never
# recalibrated since. vol-engine + cockpit both now read the dict
# directly — single source of truth.


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

    __tablename__ = "config_vol_engine"  # renamed in migration 040 (config_* prefix alignment)

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

    __tablename__ = "pca_surface_snapshot_history"  # renamed 023 → 036 (pca_* prefix alignment)
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
    """1 row per PC per vol-engine cycle. Feed Panel 2 + history charts."""

    __tablename__ = "pca_signal_history"  # renamed in migration 023
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


# ``pca_structure_recommendation`` (ORM ``SignalRecommendationsMap``) was
# dropped in migration 039. Trade suggestions were removed entirely in R11
# (migration 043 drops ``pca_signal.recommended_structure``): the desk is
# decision-support, the user picks structures — the engine doesn't propose them.


# ──────────────────────────────────────────────────────────────────────
# Step 3 — Trade preview
# ──────────────────────────────────────────────────────────────────────


# ``structure_definition_ref`` (ORM ``StructureDefinition``) was dropped
# in migration 039. The 6 catalog structures live as entries marked
# ``in_catalog=True`` in ``core.trade_preview.TEMPLATES``.


class TradePreviewRow(Base):
    """Audit log : 1 row par Arm trade."""

    __tablename__ = "trade_preview"  # renamed in migration 025
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
    # Mirror of OpenPosition.product_label (migration 032). See OpenPosition class.
    product_label: Mapped[str | None] = mapped_column(String(40))
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

    __tablename__ = "book_state_snapshot_history"  # renamed 026 → 040 (_history suffix alignment)

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


# RiskLimit + DeltaHedgeConfig folded into ``config_scalar`` (originally
# ``app_config_scalar``, renamed in migration 037). Schemas were strictly
# identical (name/value(FLOAT)/unit/description/
# is_active) so they're unified under a `namespace` discriminator. Access via
# `AppConfigScalar` with `namespace='risk'` / `namespace='delta_hedge'`.


class AppConfigScalar(Base):
    """Unified scalar config table — fold of risk_limits + delta_hedge_config.

    Each row is one tunable parameter scoped by ``namespace`` :
      - namespace='risk'        → former risk_limits rows
      - namespace='delta_hedge' → former delta_hedge_config rows

    Read patterns :
      - `select(...).where(namespace == 'risk', is_active == True)` for
        the trade preview gating (cf. api/routers/trade._load_limits).
      - `select(...).where(namespace == 'delta_hedge')` for delta-hedge
        loop config (cf. api/routers/positions delta-hedge-config endpoint).

    Hot-reloadable : UPDATE in-place. No append-only history — the
    versioned config story lives on ``vol_engine_config`` (which keeps
    its specialised shape because of Pydantic schema + admin UI).
    """

    __tablename__ = "config_scalar"  # renamed from app_config_scalar in migration 037
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


class Package(Base):
    """Operational grouping of multiple ``TradeStructure`` (= trades)
    into a single envelope — Murex's "package" concept.

    Empty by default in this project ; populated when the operator wants
    to bundle several straddles / butterflies under a single risk or
    funding key. ``trade_structure.package_id`` is the join column.

    Added in migration 034.
    """

    __tablename__ = "package"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))


class TradeStructure(Base):
    """Multi-leg trade : 1 row per Submit (new STEP3/STEP4 workflow)."""

    __tablename__ = "trade_structure"  # renamed in migration 025
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
    # Mirror of OpenPosition.product_label (migration 032). See OpenPosition class.
    product_label: Mapped[str | None] = mapped_column(String(40))
    # Migration 034 : optional grouping of multiple trades under one
    # operational package. NULL when the trade isn't part of a package.
    package_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("package.id", ondelete="SET NULL"),
    )
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
    # Migration 046 : correlation id of the request that created this trade, so a
    # trade's whole story (API → exec-engine → fills) is one `grep <trace_id>`.
    trace_id: Mapped[str | None] = mapped_column(String(32))


class StructureOrder(Base):
    """One leg of a multi-leg trade structure."""

    __tablename__ = "trade_order"  # renamed in migration 025
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
    # Migration 035 : actual IB ``localSymbol`` of the filled contract.
    # Populated by fills_handler on first fill ; exact match key for the
    # leg→trade resolution in position_sync (avoids fuzzy strike rounding).
    ib_local_symbol: Mapped[str | None] = mapped_column(String(20))
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
    # Migration 046 : correlation id (denormalised from the parent structure) so
    # the async fill callbacks can re-bind it — their logs then carry the same id
    # as the originating request. See shared.trace.
    trace_id: Mapped[str | None] = mapped_column(String(32))
    # Migration 047 (OMS P2) : for a closing order (order_role='closing'), the
    # entry order/leg it closes. Lets the reservation ledger attribute
    # reserved_qty to the exact leg (I5). NULL for entry/hedge orders.
    closes_order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trade_order.id")
    )


class StructureFill(Base):
    """One execution event on a leg."""

    __tablename__ = "trade_fill"  # renamed in migration 025

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
    # Migration 046 : correlation id (denormalised from the order) so a fill row
    # points straight back to its request's logs. See shared.trace.
    trace_id: Mapped[str | None] = mapped_column(String(32))


class LegPosition(Base):
    """Forward per-leg position — the BOOK (OMS P1, invariants I3/I7).

    One row per ``trade_order`` (a leg). ``open_qty`` is a *pure signed fold of
    that leg's fills* (Σ +buy/−sell over ``trade_fill.order_id == order.id``),
    rebuilt by ``persistence.projection`` — never back-attributed from the netted
    IB mirror (``open_position``). This is the authority for "what we hold"; the
    mirror is demoted to a reconciliation checksum (I7). ``reserved_qty`` is the
    materialised close-in-flight reservation (I5, P2); ``available = |open_qty| −
    reserved_qty`` must stay ≥ 0.
    """

    __tablename__ = "leg_position"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("trade_order.id"), nullable=False, unique=True,
    )
    open_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False, default=0)
    reserved_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False, default=0)
    avg_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    realized_pnl_usd: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    rebuilt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class ReconciliationBreak(Base):
    """Materialised book⊖broker gap per contract (OMS P1, invariant I4).

    Writer = ``engines.execution.reconciler``. A break is *data*, not an
    exception: ``resolved_at IS NULL`` is an open break; it is stamped when the
    gap closes. At most one open row per ``local_symbol`` at a time. ``book_qty``
    is Σ ``leg_position.open_qty`` for the contract (our truth); ``broker_qty`` is
    the netted IB mirror (checksum) — the mirror only ever appears here (I7).
    """

    __tablename__ = "reconciliation_break"
    __table_args__ = (
        CheckConstraint(
            "break_type IN ('missing_at_ib','unbooked_at_ib','direction','quantity')",
            name="ck_reconciliation_break_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    local_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    book_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    broker_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    diff: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    break_type: Mapped[str] = mapped_column(String(20), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BookedPosition(Base):
    """OpenPosition created when a TradeStructure is fully_filled (renamed from
    TradePosition in migration 026 Theme 2). Distinct from `OpenPosition` which
    mirrors the live IB book.

    A BookedPosition is the trade-level entity backed by 1..N OpenPosition rows
    (multi-leg structures). It carries entry costs, state machine (open →
    closing → closed/expired), exit costs, and final pnl. Step-5 consumer."""

    __tablename__ = "booked_position"  # renamed in migration 026
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

    __tablename__ = "runtime_ib_session"  # 024 → 037 (config_*) → 041 (runtime_* — semantic correction)
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


class BookedPositionMetricHistory(Base):
    """Per-cycle MTM + signal tracking for each open BookedPosition (renamed
    from PositionMtmHistory in migration 026 Theme 2 — also folded the
    former ``position_signal_tracking`` table into the 8 trailing signal_*
    columns).

    Writer = position_monitor every 60s. Signal columns are NULL for
    positions that were not opened via a triggering PCA signal."""

    __tablename__ = "booked_position_metric_history"  # renamed + folded migration 026
    __table_args__ = (
        UniqueConstraint("position_id", "timestamp", name="uq_mtm_position_ts"),
        CheckConstraint(
            "signal_status IS NULL OR signal_status IN ('HOLD','TRIM','EXIT')",
            name="ck_signal_track_status",
        ),
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

    # Signal tracking cols (folded from former position_signal_tracking).
    # All nullable: positions not opened via PCA signal keep these NULL.
    triggering_pc: Mapped[int | None] = mapped_column(Integer)
    current_z_score: Mapped[float | None] = mapped_column(Float)
    current_label: Mapped[str | None] = mapped_column(String(15))
    entry_z_score: Mapped[float | None] = mapped_column(Float)
    entry_label: Mapped[str | None] = mapped_column(String(15))
    weakening_ratio: Mapped[float | None] = mapped_column(Float)
    sign_flipped: Mapped[bool | None] = mapped_column(Boolean)
    signal_status: Mapped[str | None] = mapped_column(String(10))


# PositionSignalTracking ORM class deleted in migration 026 (Theme 2).
# Its rows were folded into BookedPositionMetricHistory via a JOIN on
# (position_id, timestamp). Query pattern post-fold:
#   select(BookedPositionMetricHistory).where(triggering_pc.is_not(None))


class HedgeOrder(Base):
    """Delta-rebalancing future order on an open position."""

    __tablename__ = "hedge_order"  # renamed in migration 025
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

    __tablename__ = "exit_alert"  # renamed in migration 025
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

    __tablename__ = "config_exit_rules"  # renamed in migration 040 (config_* prefix alignment)
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


# DeltaHedgeConfig folded into AppConfigScalar (Theme 4 migration 024).


class TradeEvent(Base):
    """Unified trade event journal — append-only (migration 025 Theme 3).

    Replaces ``execution_audit_log``. ``event_type`` is the discriminator —
    legacy values from ExecutionAuditLog are preserved verbatim (e.g.
    ``structure_filled``, ``submission_blocked``, ``order_cancelled``,
    ``position_close_initiated``, ``unwind_order_created``). New event
    families add new event_type values without schema churn.

    ``description`` carries the human-readable summary, ``payload`` the
    structured context. ``severity`` follows the standard 5-level scale.
    """

    __tablename__ = "trade_event"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('debug','info','warning','error','critical')",
            name="ck_trade_event_severity",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(15), nullable=False, default="info")
    structure_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trade_structure.id")
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trade_order.id")
    )
    position_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("booked_position.id")
    )
    description: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False, default=dict)
