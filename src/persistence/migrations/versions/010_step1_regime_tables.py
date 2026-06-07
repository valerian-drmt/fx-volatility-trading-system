"""Step 1 — Regime gating : 4 new tables + seed VRP defaults.

Cf. docs/vol_trading_pca/specs/STEP1_REGIME_GATING.md §5.

Tables :
  - regime_snapshots   : 1 row par cycle vol-engine (audit + stability check)
  - feature_history    : timeseries features (z-score rolling 90j)
  - events             : calendrier économique (event_dampener source)
  - vrp_table_default  : VRP placeholder par (regime, tenor) — 18 rows seed

Revision ID: 010_step1_regime_tables
Revises: 009_vol_tables_cleanup
Create Date: 2026-04-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "010_step1_regime_tables"
down_revision: str | None = "009_vol_tables_cleanup"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "regime_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False, server_default="EURUSD"),
        sa.Column("label", sa.String(20), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("vol_level_pct", sa.Numeric(10, 4)),
        sa.Column("vol_of_vol_pct", sa.Numeric(10, 4)),
        sa.Column("term_slope_pct", sa.Numeric(10, 4)),
        sa.Column("vol_level_z", sa.Numeric(10, 4)),
        sa.Column("vol_of_vol_z", sa.Numeric(10, 4)),
        sa.Column("term_slope_z", sa.Numeric(10, 4)),
        sa.Column("p_calm", sa.Numeric(6, 4)),
        sa.Column("p_stressed", sa.Numeric(6, 4)),
        sa.Column("p_pre_event", sa.Numeric(6, 4)),
        sa.Column("event_dampener", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("days_to_next_event", sa.Numeric(10, 4)),
        sa.Column("next_event_type", sa.String(40)),
        sa.CheckConstraint(
            "label IN ('calm','stressed','pre_event')", name="ck_regime_snapshots_label"
        ),
    )
    op.create_index(
        "ix_regime_snapshots_timestamp", "regime_snapshots", ["timestamp"], postgresql_using=None
    )
    op.create_index(
        "ix_regime_snapshots_symbol_ts", "regime_snapshots", ["symbol", "timestamp"]
    )

    op.create_table(
        "feature_history",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False, server_default="EURUSD"),
        sa.Column("iv_atm_1m_pct", sa.Numeric(10, 4)),
        sa.Column("iv_atm_3m_pct", sa.Numeric(10, 4)),
        sa.Column("iv_atm_6m_pct", sa.Numeric(10, 4)),
        sa.Column("rv_yz_pct", sa.Numeric(10, 4)),
        sa.Column("vol_of_vol_30d_pct", sa.Numeric(10, 4)),
        sa.Column("term_slope_pct", sa.Numeric(10, 4)),
        sa.Column("vol_level_z90", sa.Numeric(10, 4)),
        sa.Column("vol_of_vol_z90", sa.Numeric(10, 4)),
        sa.Column("term_slope_z90", sa.Numeric(10, 4)),
        sa.UniqueConstraint("symbol", "timestamp", name="uq_feature_history_symbol_ts"),
    )
    op.create_index(
        "ix_feature_history_symbol_ts", "feature_history", ["symbol", "timestamp"]
    )

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("impact", sa.String(10), nullable=False),
        sa.Column("region", sa.String(10), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("source_url", sa.String(500)),
        sa.Column(
            "inserted_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "impact IN ('high','medium','low')", name="ck_events_impact"
        ),
    )
    op.create_index("ix_events_scheduled_at", "events", ["scheduled_at"])

    op.create_table(
        "vrp_table_default",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("regime", sa.String(20), nullable=False),
        sa.Column("tenor", sa.String(5), nullable=False),
        sa.Column("vrp_vol_pts", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "calibration_method", sa.String(40),
            nullable=False, server_default="hardcoded_placeholder",
        ),
        sa.Column(
            "calibration_date", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("notes", sa.String(500)),
        sa.UniqueConstraint("regime", "tenor", name="uq_vrp_table_default_regime_tenor"),
        sa.CheckConstraint(
            "regime IN ('calm','stressed','pre_event')", name="ck_vrp_table_default_regime"
        ),
    )

    # Seed the 18 rows from core/vol/vrp.py VRP_DEFAULTS_VOL_PTS.
    seeds = [
        ("calm", "1M", 0.6), ("calm", "2M", 0.7), ("calm", "3M", 0.8),
        ("calm", "4M", 0.9), ("calm", "5M", 1.0), ("calm", "6M", 1.1),
        ("stressed", "1M", 1.5), ("stressed", "2M", 1.6), ("stressed", "3M", 1.8),
        ("stressed", "4M", 1.9), ("stressed", "5M", 2.0), ("stressed", "6M", 2.1),
        ("pre_event", "1M", 2.5), ("pre_event", "2M", 2.2), ("pre_event", "3M", 2.0),
        ("pre_event", "4M", 1.9), ("pre_event", "5M", 1.8), ("pre_event", "6M", 1.8),
    ]
    op.bulk_insert(
        sa.table(
            "vrp_table_default",
            sa.column("regime", sa.String),
            sa.column("tenor", sa.String),
            sa.column("vrp_vol_pts", sa.Numeric),
        ),
        [{"regime": r, "tenor": t, "vrp_vol_pts": v} for r, t, v in seeds],
    )


def downgrade() -> None:
    op.drop_table("vrp_table_default")
    op.drop_index("ix_events_scheduled_at", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_feature_history_symbol_ts", table_name="feature_history")
    op.drop_table("feature_history")
    op.drop_index("ix_regime_snapshots_symbol_ts", table_name="regime_snapshots")
    op.drop_index("ix_regime_snapshots_timestamp", table_name="regime_snapshots")
    op.drop_table("regime_snapshots")
