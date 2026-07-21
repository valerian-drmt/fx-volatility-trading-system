"""Drop ``vrp_default_curve`` — collapse onto ``core.vol.vrp.VRP_DEFAULTS_VOL_PTS``.

Why : the table was seeded by migration 010 from
``core.vol.vrp.VRP_DEFAULTS_VOL_PTS`` and has been a static, never-
recalibrated mirror of that Python dict ever since. Two sources of
truth carrying the same 18 numbers ⇒ guaranteed drift the day someone
"tunes" one without the other. Cockpit already reads the dict ; this
migration aligns vol-engine on the same source and drops the table.

Sizing : 18 rows in prod, lookup happens once per vol-engine cycle
(~2 min). The DB hit was zero-value but still a hit ; now it's pure
in-process dict access.

Future work : when an empirical VRP calibration story actually exists
(not just a "TODO Theme 4"), we'll add a *purpose-built* table that
captures the per-window time-series of fits — not a static mirror of a
hard-coded dict.

Downgrade re-creates the table + re-seeds it from
``VRP_DEFAULTS_VOL_PTS`` so rollback is loss-less.

Revision ID: 038_drop_vrp_default_curve
Revises: 037_rename_config_tables
Create Date: 2026-06-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "038_drop_vrp_default_curve"
down_revision: str | None = "037_rename_config_tables"
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
        sa.UniqueConstraint("regime", "tenor",
                            name="uq_vrp_table_default_regime_tenor"),
        sa.CheckConstraint("regime IN ('calm','stressed','pre_event')",
                           name="ck_vrp_table_default_regime"),
    )
    # Re-seed from a literal snapshot of core.vol.vrp.VRP_DEFAULTS_VOL_PTS
    # (frozen at this revision — migrations must never import live ``core``
    # modules) so the downgrade is loss-less.
    defaults_vol_pts: dict[str, dict[str, float]] = {
        "calm":      {"1M": 0.6, "2M": 0.7, "3M": 0.8, "4M": 0.9, "5M": 1.0, "6M": 1.1},
        "stressed":  {"1M": 1.5, "2M": 1.6, "3M": 1.8, "4M": 1.9, "5M": 2.0, "6M": 2.1},
        "pre_event": {"1M": 2.5, "2M": 2.2, "3M": 2.0, "4M": 1.9, "5M": 1.8, "6M": 1.8},
    }
    rows = [
        {"regime": regime, "tenor": tenor, "vrp_vol_pts": pts}
        for regime, by_tenor in defaults_vol_pts.items()
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
