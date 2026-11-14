"""SQLAlchemy ORM models for every persistence table.

All models share the same declarative Base so Alembic can diff them
together. Live ER diagram + drift detection (ORM vs DB) lives in the
dev console (``/dev`` → 🗺 DB Schema tab).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.types import TypeDecorator

JSONB_PORTABLE = JSON().with_variant(JSONB(), "postgresql")


class DescNullsLast(ColumnElement):
    """Index expression ``<column> DESC NULLS LAST`` (migration 023). SQLite's
    CREATE INDEX has no NULLS LAST, so the shared test schema gets ``DESC``;
    Postgres gets the exact expression the migration wrote, which is what the
    drift gate compares (#325)."""

    inherit_cache = True

    def __init__(self, column_name: str) -> None:
        super().__init__()
        self.column_name = column_name


@compiles(DescNullsLast)
def _compile_desc_nulls_last(element: DescNullsLast, compiler: Any, **kw: Any) -> str:
    return f"{element.column_name} DESC"


@compiles(DescNullsLast, "postgresql")
def _compile_desc_nulls_last_pg(element: DescNullsLast, compiler: Any, **kw: Any) -> str:
    return f"{element.column_name} DESC NULLS LAST"


class MoneyNumeric(TypeDecorator):
    """Exact in the database, convenient in Python (#326, audit 05#8).

    Execution money and prices used to be ``Float`` (binary) while the ledger
    tables were ``Numeric``. The column is now ``NUMERIC(p, s)``; a float, an
    int or a Decimal binds (floats go through ``str`` so 2.37 stays 2.37) and
    the value reads back as a plain float, so the call sites, the JSON
    payloads and the tests that compare with float literals are untouched.
    Policy table: ``docs/architecture/database.md``.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 15, scale: int = 2) -> None:
        super().__init__(precision, scale)

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    def process_result_value(self, value, dialect):  # type: ignore[override]
        return None if value is None else float(value)


Money = MoneyNumeric(15, 2)    # USD amounts: premium, commission, slippage, cost, P&L
Price = MoneyNumeric(18, 8)    # prices, strikes, spots
Pct = MoneyNumeric(10, 4)      # IVs in percent


class Base(DeclarativeBase):
    """Declarative base shared by every persistence model."""


class OpenPosition(Base):
    """Open position book — one row per IB contract held.

    Schema mirrors Portfolio panel section E. The IB ``localSymbol`` is the
    single canonical key (engines parse it via
    ``core.contracts.parse_local_symbol`` when they need contract specs).
    """

    __tablename__ = "open_position"  # renamed in migration 033 (was 'position')
    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_positions_side"),
        # The mirror's natural key: one row per IB contract. Created in 028 on
        # the column's old name, renamed in 029, re-asserted in 060 (#325).
        Index("ix_positions_structure_unique", "structure", unique=True),
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

    # No relationship to ``open_position_history`` any more (#330, DI-03,
    # migration 058): the history outlives the mirror row. Closing a position
    # deletes this row and nothing else; the per-leg greeks / P&L series stays
    # until the retention prune. Readers probe the history by ``position_id``
    # (index ``(position_id, timestamp)``), never through an ORM collection.
    # Note: `trades` relationship removed — Trade ORM dropped in
    # migration 025 (legacy fills journal had no live writers).


class OpenPositionHistory(Base):
    """Greeks + pnl + iv time series per IB-leg position (renamed from
    PositionSnapshot in migration 026 Theme 2). Sole writer = risk-engine.
    One row per OPEN position per 2s cycle."""

    __tablename__ = "open_position_history"  # renamed in migration 033

    __table_args__ = (
        # Migration 044: the attribution's per-position LATERAL probe and the
        # retention prune walk these (#325 declares them for the drift gate).
        Index("idx_open_position_history_position_ts", "position_id", "timestamp"),
        Index("idx_open_position_history_ts", "timestamp"),
    )

    # Schema mirrors ``open_position`` 1-to-1 plus position_id + timestamp.
    id: Mapped[int] = mapped_column(primary_key=True)
    # Plain column since migration 058 (#330): the FK with ON DELETE CASCADE
    # erased a leg's whole history when the position closed. The composite
    # index (position_id, timestamp) from migration 044 serves every reader.
    position_id: Mapped[int] = mapped_column(Integer, nullable=False)
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
    # Every reader asks for the latest row (ORDER BY timestamp DESC LIMIT 1) or
    # a time window; without this index each call sorted the whole table
    # (migration 051).
    __table_args__ = (Index("ix_account_history_ts", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Globals (in the account's base currency — typically EUR)
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

    # Per-currency breakdown (USD / EUR / etc., without the redundant BASE)
    currencies: Mapped[dict | None] = mapped_column(JSONB_PORTABLE)

    open_positions_count: Mapped[int | None] = mapped_column(Integer)


class VolSurface(Base):
    __tablename__ = "vol_surface_history"  # renamed in migration 023
    __table_args__ = (
        UniqueConstraint("timestamp", "underlying", name="uq_vol_surfaces_ts_underlying"),
        Index("ix_vol_surface_underlying_ts", "underlying", text("timestamp DESC")),  # migration 045
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
        # Natural key for the db-writer's ON CONFLICT DO NOTHING (#324,
        # migration 059): one row per vol-engine cycle per symbol.
        UniqueConstraint("symbol", "timestamp", name="uq_regime_snapshot_symbol_ts"),
        Index("ix_regime_snapshots_symbol_ts", "symbol", "timestamp"),  # migration 010
        Index("ix_regime_snapshots_timestamp", "timestamp"),  # migration 010
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
        Index("ix_feature_history_symbol_ts", "symbol", "timestamp"),  # migration 010
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
        Index("ix_events_event_hash", "event_hash"),  # migration 012
        Index("ix_events_scheduled_at", "scheduled_at"),  # migration 010
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
    __table_args__ = (
        Index("idx_vol_config_version_desc", text("version DESC")),  # migration 004
    )

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
        Index("ix_surface_snap_hourly_symbol_ts", "symbol", "timestamp"),  # migration 011
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
    __table_args__ = (
        Index("ix_pca_models_active_unique", "is_active", unique=True,
              postgresql_where=text("is_active = true")),  # migration 011: one live model
    )

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
        Index("ix_pca_signal_symbol_pc_ts", "symbol", "pc_id", text("timestamp DESC")),  # migration 045
        Index("ix_pca_signals_symbol_ts", "symbol", "timestamp"),  # migration 011
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
    """Audit log: 1 row per Arm trade."""

    __tablename__ = "trade_preview"  # renamed in migration 025
    __table_args__ = (
        CheckConstraint(
            "state IN ('valid_for_submit','blocked','expired','submitted','cancelled')",
            name="ck_trade_previews_state",
        ),
        Index("ix_trade_previews_created", "created_at"),  # migration 013
        Index("ix_trade_previews_state", "state", "created_at"),  # migration 013
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
    """Aggregated book state (1 row is_current=true per symbol + history)."""

    __tablename__ = "book_state_snapshot_history"  # renamed 026 → 040 (_history suffix alignment)
    __table_args__ = (
        Index("ix_book_state_current", "symbol", "is_current", unique=True,
              postgresql_where=text("is_current = true")),  # migration 013: one current row per symbol
    )

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
        Index("ix_app_config_scalar_ns_active", "namespace", "is_active"),  # migration 024
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
            "state IN ('submitted','fully_filled','partial_fail','fully_failed','closed')",
            name="ck_trade_structures_state",
        ),
        Index("ix_trade_structures_state", "state", "created_at"),  # migration 014
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # One structure per preview (migration 053, #319): the last line against a
    # double submit. Closing / hedge structures carry NULL, which UNIQUE allows.
    preview_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("trade_preview.preview_id"), unique=True,
    )
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
    total_premium_paid_usd: Mapped[float | None] = mapped_column(Money)
    total_slippage_usd: Mapped[float | None] = mapped_column(Money)
    total_commission_usd: Mapped[float | None] = mapped_column(Money)
    total_entry_cost_usd: Mapped[float | None] = mapped_column(Money)
    first_fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fully_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(80))
    # Migration 046 : correlation id of the request that created this trade, so a
    # trade's whole story (API → exec-engine → fills) is one `grep <trace_id>`.
    trace_id: Mapped[str | None] = mapped_column(String(32))
    # Migration 050 : execution-safety claims (EXEC-1 / EXEC-3).
    # submit_claimed_at is taken atomically (UPDATE ... WHERE IS NULL) and
    # committed BEFORE any IB placement — a replayed live submit finds it set
    # and is refused, so a retry can never double-place the legs. Cleared when
    # the submit fails before any placement (retry stays possible).
    submit_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # rollback_started_at serialises concurrent rollbacks + stamps the audit
    # trail; the double-unwind safety itself is the residual-qty math in
    # core.execution.rollback.
    rollback_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StructureOrder(Base):
    """One leg of a multi-leg trade structure."""

    __tablename__ = "trade_order"  # renamed in migration 025
    __table_args__ = (
        UniqueConstraint(
            "structure_id", "leg_idx", "order_role",
            name="uq_structure_orders_structure_leg_role",
        ),
        CheckConstraint(
            "state IN ('pending','submitted','partially_filled','filled','rejected','cancelled','expired')",
            name="ck_structure_orders_state",
        ),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_structure_orders_side"),
        CheckConstraint(
            "order_role IN ('entry','closing','unwind','hedge')",
            name="ck_structure_orders_order_role",
        ),
        Index("ix_structure_orders_structure", "structure_id", "leg_idx"),  # migration 014
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
    # IB contract id of the contract actually filled (#460), stamped with
    # ``ib_local_symbol`` on the first fill. NULL until then.
    con_id: Mapped[int | None] = mapped_column(BigInteger)
    contract_symbol: Mapped[str] = mapped_column(String(10), nullable=False, default="EUR")
    contract_type: Mapped[str] = mapped_column(String(10), nullable=False)
    contract_expiry: Mapped[date | None] = mapped_column(Date)
    contract_strike: Mapped[float | None] = mapped_column(Price)
    contract_exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="CME")
    contract_currency: Mapped[str] = mapped_column(String(5), nullable=False, default="USD")
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False, default="LMT")
    limit_price: Mapped[float | None] = mapped_column(Price)
    time_in_force: Mapped[str] = mapped_column(String(5), nullable=False, default="DAY")
    preview_iv_pct: Mapped[float | None] = mapped_column(Pct)
    preview_price: Mapped[float | None] = mapped_column(Price)
    state: Mapped[str] = mapped_column(String(25), nullable=False)
    state_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Placement claim (migration 055, #318): set atomically before this row's
    # order is handed to IB; a replay finds it non-NULL and is refused.
    submit_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the desk asked the broker to cancel this order (#485). An order the
    # broker ends without it was not cancelled by us: it expired.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_code: Mapped[str | None] = mapped_column(String(20))
    rejection_text: Mapped[str | None] = mapped_column(String(300))
    qty_filled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Price)
    total_commission_usd: Mapped[float | None] = mapped_column(Money, default=0.0)
    fully_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slippage_per_contract: Mapped[float | None] = mapped_column(Price)
    total_slippage_usd: Mapped[float | None] = mapped_column(Money)
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
    __table_args__ = (
        Index("ix_structure_fills_order", "order_id", "timestamp"),  # migration 014
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_order.id"), nullable=False)
    ib_execution_id: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qty_filled: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[float] = mapped_column(Price, nullable=False)
    commission_usd: Mapped[float] = mapped_column(Money, nullable=False, default=0.0)
    # Currency of the fill / commission as IB reports it (migration 056, #322):
    # a 6E leg is USD, but nothing recorded it, and a CommissionReport can be
    # in another currency than the contract.
    currency: Mapped[str | None] = mapped_column(String(5))
    exchange: Mapped[str | None] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    spot_at_fill: Mapped[float | None] = mapped_column(Price)
    bid_at_fill: Mapped[float | None] = mapped_column(Price)
    ask_at_fill: Mapped[float | None] = mapped_column(Price)
    iv_implied_from_fill: Mapped[float | None] = mapped_column(Pct)
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
        Index("ix_reconciliation_break_open", "local_symbol",
              postgresql_where=text("resolved_at IS NULL")),  # migration 048
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


class Instrument(Base):
    """One row per contract the broker has ever shown us, keyed by the IB
    contract id (#460). The identity every book-to-broker comparison uses:
    type, multiplier, listed expiry and strike come from IB, never from a
    symbol rebuilt out of a preview's numbers. Upserted by the execution
    engine from every position sync and every fill."""
    __tablename__ = "instrument"
    __table_args__ = (
        Index("ix_instrument_local_symbol", "local_symbol"),
    )
    con_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    local_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    sec_type: Mapped[str] = mapped_column(String(8), nullable=False)    # FOP / FUT / CASH
    symbol: Mapped[str | None] = mapped_column(String(10))
    currency: Mapped[str | None] = mapped_column(String(5))
    exchange: Mapped[str | None] = mapped_column(String(12))
    multiplier: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    option_right: Mapped[str | None] = mapped_column(String(1))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class PositionEvent(Base):
    """A change to a leg's position that is NOT an execution (#463).

    Fills are the only input of the book as long as nothing else moves a
    position. Three things do: a contract expires (IB drops it, no fill), an
    option is exercised or assigned, and a repair stands in for a fill the
    engine never received. Each is one row here, folded with the fills by
    ``persistence.projection``; nothing else may change ``leg_position``.

    ``side`` / ``qty`` / ``price`` read like a fill on the leg: an expiry of a
    long leg is a SELL of the open quantity at 0 (or at the settlement price).
    ``dedup_key`` makes the writers idempotent (one expiry per leg and date).
    """
    __tablename__ = "position_event"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('expiry','exercise','assignment','repair','writeoff')",
            name="ck_position_event_kind",
        ),
        CheckConstraint("side IN ('BUY','SELL')", name="ck_position_event_side"),
        UniqueConstraint("order_id", "kind", "dedup_key", name="uq_position_event_dedup"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_order.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(15, 8), nullable=False, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(60), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


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
        Index("ix_trade_positions_ib_reconciled_at", DescNullsLast("ib_reconciled_at")),  # migration 023
        Index("ix_trade_positions_state", "state", "opened_at"),  # migration 014
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    structure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trade_structure.id"), nullable=False, unique=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_premium_usd: Mapped[float] = mapped_column(Money, nullable=False)
    entry_total_cost_usd: Mapped[float] = mapped_column(Money, nullable=False)
    state: Mapped[str] = mapped_column(String(15), nullable=False, default="open")
    state_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    entry_vega_usd_per_volpt: Mapped[float | None] = mapped_column(Float)
    entry_gamma_usd_per_pip2: Mapped[float | None] = mapped_column(Float)
    entry_theta_usd_per_day: Mapped[float | None] = mapped_column(Float)
    entry_spot: Mapped[float | None] = mapped_column(Price)
    entry_iv_avg: Mapped[float | None] = mapped_column(Pct)
    entry_regime: Mapped[str | None] = mapped_column(String(20))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(80))
    exit_premium_usd: Mapped[float | None] = mapped_column(Money)
    exit_total_cost_usd: Mapped[float | None] = mapped_column(Money)
    gross_pnl_usd: Mapped[float | None] = mapped_column(Money)
    net_pnl_usd: Mapped[float | None] = mapped_column(Money)
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


class SpotOrder(Base):
    """One spot FX (secType CASH, e.g. EUR.USD on IDEALPRO) order placed from the
    Trade tab's cash ticket.

    Deliberately NOT a ``trade_structure`` / ``trade_order`` pair: spot cash
    management must stay out of the structure machinery (booking, reaper,
    reservation ledger, position sync). The execution-engine writes the row
    before placing at IB and its status/fill callbacks keep ``state`` /
    ``qty_filled`` current ; the Orders blotter reads it via
    ``GET /trade/spot-orders`` (migration 052).
    """

    __tablename__ = "spot_order"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True,
    )
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, default="EUR")
    currency: Mapped[str] = mapped_column(String(5), nullable=False, default="USD")
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="IDEALPRO")
    side: Mapped[str] = mapped_column(String(5), nullable=False)      # BUY / SELL (base ccy)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)          # base-ccy notional
    order_type: Mapped[str] = mapped_column(String(10), nullable=False, default="LMT")
    limit_price: Mapped[float | None] = mapped_column(Price)
    ib_order_id: Mapped[str | None] = mapped_column(String(40))
    ib_perm_id: Mapped[str | None] = mapped_column(String(40))
    ib_local_symbol: Mapped[str | None] = mapped_column(String(20))   # "EUR.USD"
    # pending → submitted → partially_filled → filled | cancelled | rejected
    state: Mapped[str] = mapped_column(String(25), nullable=False, default="pending")
    state_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    qty_filled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Price)
    commission_usd: Mapped[float | None] = mapped_column(Money)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_text: Mapped[str | None] = mapped_column(String(300))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    # Raw IB view of a working order (migration 054, #376): ``ib_status`` is
    # IB's own string (PreSubmitted / Submitted / ...), ``why_held`` its hint for
    # a held order. Both are what the blotter needs to say why a row waits.
    ib_status: Mapped[str | None] = mapped_column(String(20))
    why_held: Mapped[str | None] = mapped_column(String(60))


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
        Index("ix_mtm_position_ts", "position_id", "timestamp"),  # migration 016
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
        Index("ix_hedge_position_ts", "position_id", "triggered_at"),  # migration 016
        # One PENDING hedge per position (migration 060, #325): the hedge loop
        # must never queue two for the same leg.
        Index(
            "uq_hedge_order_pending_per_position", "position_id", unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("booked_position.id"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Placement claim (migration 055, #318), same semantics as trade_order.
    submit_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delta_imbalance_at_trigger: Mapped[float] = mapped_column(Float, nullable=False)
    rebalance_threshold_used: Mapped[float] = mapped_column(Float, nullable=False)
    hedge_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    ib_order_id: Mapped[str | None] = mapped_column(String(40))
    fill_price: Mapped[float | None] = mapped_column(Price)
    commission_usd: Mapped[float | None] = mapped_column(Money)
    spread_paid_usd: Mapped[float | None] = mapped_column(Money)
    total_cost_usd: Mapped[float | None] = mapped_column(Money)
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
        Index("ix_exit_alerts_position_ts", "position_id", "timestamp"),  # migration 016
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
        Index("ix_trade_event_structure", "structure_id",
              postgresql_where=text("structure_id IS NOT NULL")),  # migration 025
        Index("ix_trade_event_ts", text("ts DESC")),  # migration 025
        Index("ix_trade_event_type_ts", "event_type", text("ts DESC")),  # migration 025
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


# ──────────────────────────────────────────────────────────────────────
# Materialised portfolio rollups (#368, hot/cold T4). Writer = analytics
# engine every ROLLUP_INTERVAL_S; readers = the daily portfolio endpoints.
# ──────────────────────────────────────────────────────────────────────
class PortfolioDaily(Base):
    """One row per UTC day of account history: net-liq open / close, the
    day-over-day MTM, realised P&L of genuine closes. Rebuilt from
    ``account_history`` + ``booked_position`` by ``persistence.rollups``."""
    __tablename__ = "portfolio_daily"
    __table_args__ = (UniqueConstraint("day", name="uq_portfolio_daily_day"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    net_liq_open: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    net_liq_close: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    mtm_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    realized_usd: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    n_closed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_sample_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sample_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PnlAttributionLatest(Base):
    """The latest per-position P&L attribution for one lookback window, as
    the analytics engine computed it (``core.risk.attribution``). The api
    filters / pivots the stored rows; it never recomputes on the request path
    while a fresh row exists."""
    __tablename__ = "pnl_attribution_latest"
    __table_args__ = (
        UniqueConstraint("lookback_hours", name="uq_pnl_attribution_latest_lookback"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lookback_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forward_source: Mapped[str] = mapped_column(String(20), nullable=False)
    n_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    per_position: Mapped[list] = mapped_column(JSONB_PORTABLE, nullable=False, default=list)


class DbWriterDeadLetter(Base):
    """A streamed row the db-writer could not insert on its own after the
    per-row fallback (#324, DI-04): kept with the error instead of being
    dropped with its batch. Replay is manual (``docs/ops/redis-durability.md``)."""
    __tablename__ = "db_writer_dead_letter"
    __table_args__ = (
        Index("ix_db_writer_dead_letter_created", "created_at"),  # migration 059
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB_PORTABLE, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- Table comments (migration 042) --------------------------------------------------------
# ``COMMENT ON TABLE`` for every business table, applied to the metadata so the
# drift gate (``alembic check``, #325) sees the same comments the migration
# wrote. One place, alphabetised; tests/unit/persistence/test_schema_declarations.py
# checks this map equals the migration's.
TABLE_COMMENTS: dict[str, str] = {
    "account_history":
        "Per-cycle audit snapshot of the IB account (cash, margin, gross/net liq). "
        "Append-only ; written by execution-engine on each account-summary update.",
    "book_state_snapshot_history":
        "Audit log of every successful Submit — captures the full book state right "
        "after the new trade is open. Append-only.",
    "booked_position":
        "User-level trades booked into the book (1 row per Submit). "
        "Append-only ; lifecycle states tracked via state column.",
    "booked_position_metric_history":
        "Per-cycle greeks + P&L snapshot per booked_position row. "
        "Append-only ; written by risk-engine every cycle.",
    "config_exit_rules":
        "Versioned set of systematic exit rules (z-flip / time / stop-loss / "
        "DTE thresholds). Append-only ; latest row is the source of truth.",
    "config_scalar":
        "Generic scalar key/value config — namespace='risk' for trade-gating "
        "limits, namespace='delta_hedge' for the hedger loop. UPDATE-in-place.",
    "config_vol_engine":
        "Versioned full vol-engine config (Pydantic JSON blob). Append-only ; "
        "engines hot-reload via Redis pub/sub on insert.",
    "event_calendar":
        "Catalogue of economic events scraped from FRED / ECB / BoE / FOMC / "
        "Eurostat / ONS. Idempotent UPSERT keyed by event_hash.",
    "exit_alert":
        "Triggered exit alerts pending operator review. "
        "Append-only ; consumed by the exit-alert panel.",
    "feature_history":
        "Wide-format timeseries of derived vol features (ATM IV per tenor, "
        "rv_yz, vol_of_vol, term_slope, z-scores). Append-only.",
    "hedge_order":
        "EUR FUT delta-hedge orders submitted by the delta-hedger loop. "
        "Append-only ; one row per fire.",
    "open_position":
        "Live IB-side positions reconciled against ib_async. UPDATE-in-place "
        "by position-sync (5 s cadence) ; row deleted when fully closed.",
    "open_position_history":
        "Per-cycle greeks + P&L snapshot per open_position row. Append-only ; "
        "written by risk-engine every cycle.",
    "package":
        "Murex-style operational grouping of multiple trade_structure rows. "
        "Populated by manual API call ; FK target of trade_structure.package_id.",
    "pca_model":
        "Versioned PCA fit (means / stds / loadings / eigenvalues as JSONB). "
        "Append-only ; is_active=true marks the live model.",
    "pca_signal_history":
        "One row per (PC, cycle) — z-score, label (CHEAP/FAIR/EXPENSIVE), "
        "actionable flag, recommended structure. Append-only.",
    "pca_surface_snapshot_history":
        "30-dimensional hourly snapshot of the vol surface (6 tenors x 5 "
        "deltas). The PCA fit input. Append-only.",
    "regime_snapshot_history":
        "One row per vol-engine cycle — regime label, the features that drove "
        "the classification, GMM probas, event_dampener. Append-only.",
    "runtime_ib_session":
        "Singleton row tracking the IB session state — heartbeat, account "
        "type (paper/live), cash + margin. UPDATE-in-place every ~10 s.",
    "trade_event":
        "Audit log of every order action (submit, cancel, fill, reject) tied "
        "to a trade_structure row. Append-only ; replaces the legacy "
        "execution_audit_log.",
    "trade_fill":
        "Per-leg fill confirmations from IB. Append-only ; one row per fill, "
        "linked to trade_order.id.",
    "trade_order":
        "Active IB orders submitted as part of a trade_structure. "
        "UPDATE-in-place on status transitions ; long-lived for audit.",
    "trade_preview":
        "Audit log : one row per Arm click on the UI. Tracks the (signal, "
        "structure, state) tuple for 90 s — either Submit promotes it to a "
        "trade_structure or it expires.",
    "trade_structure":
        "Root of the Murex package > trade > contract hierarchy. One row per "
        "user-confirmed Submit ; lifecycle tracked via state column.",
    "vol_surface_history":
        "Per-cycle full vol surface fit (SVI/SSVI params + ATM per tenor) "
        "stored as JSONB. Append-only ; written by vol-engine each cycle.",
}

for _table_name, _comment in TABLE_COMMENTS.items():
    Base.metadata.tables[_table_name].comment = _comment
