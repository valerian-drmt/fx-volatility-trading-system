"""vol tables : drop unused JSONB cols + add P-measure/VRP cols to signals.

Cf. docs/VOL_DISCREPANCY_REPORT.md :
  - vol_surfaces.fair_vol_data + rv_data : "(reserved, null)" never written
  - signals : engine.py:285-287 produit sigma_fair_p_pct + vrp_vol_pts
    mais le schéma DB ne les stocke pas → infos perdues à chaque cycle.

Revision ID: 009_vol_tables_cleanup
Revises: 008_account_snaps_cleanup
Create Date: 2026-04-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009_vol_tables_cleanup"
down_revision: str | None = "008_account_snaps_cleanup"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Drop reserved-but-empty JSONB cols in vol_surfaces.
    op.drop_column("vol_surfaces", "fair_vol_data")
    op.drop_column("vol_surfaces", "rv_data")

    # Add P-measure + VRP cols to signals (matches engine.py:285-287 output).
    op.add_column("signals", sa.Column("sigma_fair_p", sa.Numeric(8, 5), nullable=True))
    op.add_column("signals", sa.Column("vrp_vol_pts", sa.Numeric(8, 5), nullable=True))


def downgrade() -> None:
    op.drop_column("signals", "vrp_vol_pts")
    op.drop_column("signals", "sigma_fair_p")
    op.add_column("vol_surfaces", sa.Column(
        "rv_data",
        postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
        nullable=True,
    ))
    op.add_column("vol_surfaces", sa.Column(
        "fair_vol_data",
        postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
        nullable=True,
    ))
