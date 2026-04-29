"""account_snaps : drop NULL cols + ajouter métriques utiles.

  - DROP buying_power_usd, available_usd, realized_pnl_usd,
         gross_position_value_usd  (toujours NULL en pratique sur les
         comptes IB où ces tags ne sont pas exposés directement).
  - ADD  accrued_cash, cushion, init_margin_req, maint_margin_req,
         excess_liquidity, gross_position_value
         (ces métriques vivent typiquement dans la currency du compte).

Revision ID: 008_account_snaps_cleanup
Revises: 007_add_order_events_table
Create Date: 2026-04-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "008_account_snaps_cleanup"
down_revision: str | None = "007_add_order_events_table"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("account_snaps", "buying_power_usd")
    op.drop_column("account_snaps", "available_usd")
    op.drop_column("account_snaps", "realized_pnl_usd")
    op.drop_column("account_snaps", "gross_position_value_usd")
    op.add_column("account_snaps", sa.Column("accrued_cash", sa.Numeric(15, 2), nullable=True))
    op.add_column("account_snaps", sa.Column("cushion", sa.Numeric(8, 4), nullable=True))
    op.add_column("account_snaps", sa.Column("init_margin_req", sa.Numeric(15, 2), nullable=True))
    op.add_column("account_snaps", sa.Column("maint_margin_req", sa.Numeric(15, 2), nullable=True))
    op.add_column("account_snaps", sa.Column("excess_liquidity", sa.Numeric(15, 2), nullable=True))
    op.add_column("account_snaps", sa.Column("gross_position_value", sa.Numeric(15, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("account_snaps", "gross_position_value")
    op.drop_column("account_snaps", "excess_liquidity")
    op.drop_column("account_snaps", "maint_margin_req")
    op.drop_column("account_snaps", "init_margin_req")
    op.drop_column("account_snaps", "cushion")
    op.drop_column("account_snaps", "accrued_cash")
    op.add_column("account_snaps", sa.Column("gross_position_value_usd", sa.Numeric(15, 2), nullable=True))
    op.add_column("account_snaps", sa.Column("realized_pnl_usd", sa.Numeric(15, 2), nullable=True))
    op.add_column("account_snaps", sa.Column("available_usd", sa.Numeric(15, 2), nullable=True))
    op.add_column("account_snaps", sa.Column("buying_power_usd", sa.Numeric(15, 2), nullable=True))
