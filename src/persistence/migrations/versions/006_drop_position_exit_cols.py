"""Drop positions.exit_price / positions.exit_timestamp.

Le close timestamp + close price sont reconstructibles depuis la dernière
row trades(position_id), donc redondants. Décision R9 sandbox : on simplifie
le schema positions, qui ne porte plus que (entry, status, current_qty).

Revision ID: 006_drop_position_exit_cols
Revises: 005_add_orders_table
Create Date: 2026-04-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "006_drop_position_exit_cols"
down_revision: str | None = "005_add_orders_table"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("positions", "exit_price")
    op.drop_column("positions", "exit_timestamp")


def downgrade() -> None:
    op.add_column("positions", sa.Column("exit_price", sa.Numeric(15, 8), nullable=True))
    op.add_column("positions", sa.Column("exit_timestamp", sa.DateTime(timezone=True), nullable=True))
