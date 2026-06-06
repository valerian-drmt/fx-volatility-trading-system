"""Add orders table — track IB order lifecycle.

Revision ID: 005_add_orders_table
Revises: 004_add_vol_config_table
Create Date: 2026-04-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "005_add_orders_table"
down_revision: str | None = "004_add_vol_config_table"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ib_perm_id", sa.BigInteger, nullable=True),
        sa.Column("ib_order_id", sa.Integer, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("sec_type", sa.String(10), nullable=False),
        sa.Column("expiry", sa.String(10), nullable=True),
        sa.Column("strike", sa.Numeric(10, 5), nullable=True),
        sa.Column("right", sa.String(2), nullable=True),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
        sa.Column("limit_price", sa.Numeric(15, 8), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("filled_qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Numeric(15, 8), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("ib_perm_id", name="uq_orders_ib_perm_id"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_orders_side"),
        sa.CheckConstraint("sec_type IN ('FUT', 'FOP', 'STK', 'OPT', 'CONTFUT')", name="ck_orders_sec_type"),
    )
    op.create_index("idx_orders_status", "orders", ["status"])
    op.create_index("idx_orders_submitted_at", "orders", [sa.text("submitted_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_orders_submitted_at", table_name="orders")
    op.drop_index("idx_orders_status", table_name="orders")
    op.drop_table("orders")
