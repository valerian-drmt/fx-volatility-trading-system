"""Drop the per-tenor pricing signal table.

After R9 the trading strategy is PCA-only. The CHEAP / FAIR / EXPENSIVE
per-tenor signal pipeline (OHLC → GARCH/HAR → VRP → σ_fair^Q → ecart)
was retired alongside the table that stored its output. This is
irreversible — downgrade re-creates an empty stub but historical rows
are gone.

Revision ID: 021_drop_pricing_signal_snapshot
Revises: 020_rename_vol_tables
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "021_drop_pricing_signal_snapshot"
down_revision: str | None = "020_rename_vol_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("vol_pricing_signal_snapshot")
    # scan_duration_s on vol_surface_snapshot was never populated by the live
    # writer — drop the dead column.
    op.drop_column("vol_surface_snapshot", "scan_duration_s")


def downgrade() -> None:
    op.add_column(
        "vol_surface_snapshot",
        sa.Column("scan_duration_s", sa.Numeric(6, 2), nullable=True),
    )
    op.create_table(
        "vol_pricing_signal_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("underlying", sa.String(20), nullable=False),
        sa.Column("tenor", sa.String(5), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=False),
        sa.Column("sigma_mid", sa.Numeric(8, 5), nullable=False),
        sa.Column("sigma_fair", sa.Numeric(8, 5), nullable=False),
        sa.Column("ecart", sa.Numeric(8, 5), nullable=False),
        sa.Column("signal_type", sa.String(15), nullable=False),
        sa.Column("rv", sa.Numeric(8, 5)),
        sa.Column("sigma_fair_p", sa.Numeric(8, 5)),
        sa.Column("vrp_vol_pts", sa.Numeric(8, 5)),
        sa.UniqueConstraint(
            "timestamp", "underlying", "tenor",
            name="uq_signals_ts_underlying_tenor",
        ),
        sa.CheckConstraint(
            "signal_type IN ('CHEAP', 'EXPENSIVE', 'FAIR')",
            name="ck_signals_signal_type",
        ),
    )
