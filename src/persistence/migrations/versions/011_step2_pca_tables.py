"""Step 2 — PCA factor model tables + seed signal_recommendations_map.

Cf. docs/vol_trading_pca/specs/STEP2_SIGNAL_DETECTION.md §5.

Tables :
  - surface_snapshots_hourly    : 30-dim (6 tenors × 5 deltas) snapshot horaire
  - pca_models                  : 1 row par refit, JSONB means/stds/loadings
  - pca_signals                 : 1 row par PC par cycle vol-engine
  - signal_recommendations_map  : lookup PC × label → structure (6 rows seed)

Pas de pca_stability_log dans cette migration : les diagnostics cosine_similarity_*
+ sign_flip_* sont stockés directement dans pca_models pour rester compact.

Revision ID: 011_step2_pca_tables
Revises: 010_step1_regime_tables
Create Date: 2026-04-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_step2_pca_tables"
down_revision: str | None = "010_step1_regime_tables"
branch_labels: str | None = None
depends_on: str | None = None

JSONB_PORTABLE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
TENORS = ("1m", "2m", "3m", "4m", "5m", "6m")
DELTAS = ("10dp", "25dp", "atm", "25dc", "10dc")


def upgrade() -> None:
    iv_cols = [
        sa.Column(f"iv_{t}_{d}", sa.Numeric(10, 6))
        for t in TENORS for d in DELTAS
    ]
    op.create_table(
        "surface_snapshots_hourly",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False, server_default="EURUSD"),
        *iv_cols,
        sa.Column("source", sa.String(40), nullable=False, server_default="live_engine"),
        sa.Column("spot_at_snapshot", sa.Numeric(15, 8)),
        sa.Column("n_strikes_present", sa.Integer),
        sa.Column("has_no_arb_violation", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("symbol", "timestamp", name="uq_surface_snap_hourly_symbol_ts"),
    )
    op.create_index(
        "ix_surface_snap_hourly_symbol_ts", "surface_snapshots_hourly",
        ["symbol", "timestamp"],
    )

    op.create_table(
        "pca_models",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("version", sa.String(60), nullable=False, unique=True),
        sa.Column(
            "fit_timestamp", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("fit_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fit_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n_obs_used", sa.Integer, nullable=False),
        sa.Column("means", JSONB_PORTABLE, nullable=False),
        sa.Column("stds", JSONB_PORTABLE, nullable=False),
        sa.Column("loadings", JSONB_PORTABLE, nullable=False),
        sa.Column("eigenvalues", JSONB_PORTABLE, nullable=False),
        sa.Column("variance_explained_ratio", JSONB_PORTABLE, nullable=False),
        sa.Column("n_components_kept", sa.Integer, nullable=False, server_default="6"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("superseded_by", sa.BigInteger, sa.ForeignKey("pca_models.id")),
        sa.Column("cosine_similarity_pc1", sa.Numeric(8, 6)),
        sa.Column("cosine_similarity_pc2", sa.Numeric(8, 6)),
        sa.Column("cosine_similarity_pc3", sa.Numeric(8, 6)),
        sa.Column("sign_flip_pc1", sa.Boolean),
        sa.Column("sign_flip_pc2", sa.Boolean),
        sa.Column("sign_flip_pc3", sa.Boolean),
        sa.Column("notes", sa.String(500)),
    )
    op.create_index(
        "ix_pca_models_active_unique", "pca_models", ["is_active"],
        unique=True, postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "pca_signals",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False, server_default="EURUSD"),
        sa.Column(
            "pca_model_id", sa.BigInteger,
            sa.ForeignKey("pca_models.id"), nullable=False,
        ),
        sa.Column("pc_id", sa.Integer, nullable=False),
        sa.Column("raw_score", sa.Numeric(15, 8), nullable=False),
        sa.Column("z_score", sa.Numeric(10, 4), nullable=False),
        sa.Column("label", sa.String(15), nullable=False),
        sa.Column("actionable", sa.Boolean, nullable=False),
        sa.Column("actionable_reason", sa.String(80)),
        sa.Column("sub_signals", JSONB_PORTABLE),
        sa.Column("recommended_structure", sa.String(80)),
        sa.UniqueConstraint(
            "symbol", "timestamp", "pca_model_id", "pc_id",
            name="uq_pca_signals_symbol_ts_model_pc",
        ),
        sa.CheckConstraint(
            "label IN ('CHEAP','FAIR','EXPENSIVE')", name="ck_pca_signals_label",
        ),
        sa.CheckConstraint("pc_id > 0", name="ck_pca_signals_pc_id"),
    )
    op.create_index("ix_pca_signals_symbol_ts", "pca_signals", ["symbol", "timestamp"])

    op.create_table(
        "signal_recommendations_map",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pc_id", sa.Integer, nullable=False),
        sa.Column("signal_label", sa.String(15), nullable=False),
        sa.Column("recommended_structure", sa.String(60), nullable=False),
        sa.Column("default_tenor", sa.String(10), nullable=False),
        sa.Column("description", sa.String(200)),
        sa.Column("rationale", sa.String(500)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "pc_id", "signal_label", "is_active",
            name="uq_signal_rec_map_pc_label_active",
        ),
        sa.CheckConstraint(
            "signal_label IN ('CHEAP','EXPENSIVE')",
            name="ck_signal_rec_map_label",
        ),
    )

    seeds = [
        (1, "CHEAP", "straddle_atm", "3M",
         "Long straddle ATM",
         "PC1 CHEAP = vol level low → buy vol via ATM straddle"),
        (1, "EXPENSIVE", "short_strangle", "3M",
         "Short OTM strangle",
         "PC1 EXPENSIVE = vol level high → sell vol via OTM strangle"),
        (2, "CHEAP", "calendar_long", "1M_3M",
         "Calendar buying long tenor",
         "PC2 CHEAP = term slope inverted → buy long tenor"),
        (2, "EXPENSIVE", "calendar_short", "1M_3M",
         "Calendar selling long tenor",
         "PC2 EXPENSIVE = term slope steep → sell long tenor"),
        (3, "CHEAP", "long_butterfly_25d", "3M",
         "Long 25d butterfly",
         "PC3 CHEAP = wings cheap relative to ATM"),
        (3, "EXPENSIVE", "short_butterfly_25d", "3M",
         "Short 25d butterfly",
         "PC3 EXPENSIVE = wings rich relative to ATM"),
    ]
    op.bulk_insert(
        sa.table(
            "signal_recommendations_map",
            sa.column("pc_id", sa.Integer),
            sa.column("signal_label", sa.String),
            sa.column("recommended_structure", sa.String),
            sa.column("default_tenor", sa.String),
            sa.column("description", sa.String),
            sa.column("rationale", sa.String),
        ),
        [
            {
                "pc_id": p, "signal_label": lab, "recommended_structure": s,
                "default_tenor": t, "description": d, "rationale": r,
            }
            for p, lab, s, t, d, r in seeds
        ],
    )


def downgrade() -> None:
    op.drop_table("signal_recommendations_map")
    op.drop_index("ix_pca_signals_symbol_ts", table_name="pca_signals")
    op.drop_table("pca_signals")
    op.drop_index("ix_pca_models_active_unique", table_name="pca_models")
    op.drop_table("pca_models")
    op.drop_index(
        "ix_surface_snap_hourly_symbol_ts", table_name="surface_snapshots_hourly",
    )
    op.drop_table("surface_snapshots_hourly")
