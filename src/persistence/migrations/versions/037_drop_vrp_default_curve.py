"""Drop ``vrp_default_curve`` — collapse onto ``core.vol.vrp.VRP_DEFAULTS_VOL_PTS``.

The table was seeded by migration 010 from ``VRP_DEFAULTS_VOL_PTS`` and has been
a static, never-recalibrated mirror of that Python dict ever since (18 rows, read
once per vol-engine cycle). Two sources of truth for the same numbers ⇒ drift
risk for zero value. Cockpit + vol-engine already read the dict ; this drops the
dead mirror table. Downgrade recreates + re-seeds from the dict (loss-less).

Closes the R10.1 schema reconciliation : the remaining r10 drops/folds
(regime_pattern_dict, pca_structure_recommendation, structure_definitions,
trades/orders/order_events, execution_audit_log→trade_event,
position_signal_tracking) are feature-coupled (live readers on main) and belong
to R10.2.

Revision ID: 037_drop_vrp_default_curve
Revises: 036_config_feature_renames
Create Date: 2026-06-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "037_drop_vrp_default_curve"
down_revision: str | None = "036_config_feature_renames"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("vrp_default_curve")


def downgrade() -> None:
    op.create_table(
        "vrp_default_curve",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("regime", sa.String(length=20), nullable=False),
        sa.Column("tenor", sa.String(length=5), nullable=False),
        sa.Column("vrp_vol_pts", sa.Numeric(8, 4), nullable=False),
        sa.Column("calibration_method", sa.String(length=40),
                  nullable=False, server_default="hardcoded_placeholder"),
        sa.Column("calibration_date", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_vrp_table_default"),
        sa.UniqueConstraint("regime", "tenor", name="uq_vrp_table_default_regime_tenor"),
        sa.CheckConstraint("regime IN ('calm','stressed','pre_event')",
                           name="ck_vrp_table_default_regime"),
    )
    from core.vol.vrp import VRP_DEFAULTS_VOL_PTS
    rows = [
        {"regime": regime, "tenor": tenor, "vrp_vol_pts": pts}
        for regime, by_tenor in VRP_DEFAULTS_VOL_PTS.items()
        for tenor, pts in by_tenor.items()
    ]
    op.bulk_insert(
        sa.table(
            "vrp_default_curve",
            sa.column("regime", sa.String),
            sa.column("tenor", sa.String),
            sa.column("vrp_vol_pts", sa.Numeric),
        ),
        rows,
    )
