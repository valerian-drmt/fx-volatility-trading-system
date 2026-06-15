"""3-level Murex-aligned identity stack + IB local symbol on trade_order.

R10.2 (schema for 2.3/2.4). Additive, all-nullable :
  - new ``package`` table (header + label) — empty until an operator groups trades.
  - ``trade_structure.package_id`` FK package.
  - ``open_position`` / ``open_position_history`` : contract_id (IB conId),
    trade_id (FK trade_structure), package_id (FK package). FKs ON DELETE SET NULL
    so a position survives deletion of its trade / package upstream.
  - ``trade_order.ib_local_symbol`` — exact leg→trade match key (set by fills_handler).

Combines r10 migrations 034 + 035. Ref r11.

Revision ID: 041_product_identity_schema
Revises: 040_product_label_column
Create Date: 2026-06-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "041_product_identity_schema"
down_revision: str | None = "040_product_label_column"
branch_labels: str | None = None
depends_on: str | None = None

_POS_TABLES = ("open_position", "open_position_history")


def upgrade() -> None:
    op.create_table(
        "package",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
    )
    op.add_column("trade_structure", sa.Column("package_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_trade_structure_package_id", "trade_structure", "package",
        ["package_id"], ["id"], ondelete="SET NULL",
    )
    for table in _POS_TABLES:
        op.add_column(table, sa.Column("contract_id", sa.BigInteger(), nullable=True))
        op.add_column(table, sa.Column("trade_id", sa.BigInteger(), nullable=True))
        op.add_column(table, sa.Column("package_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_trade_id", table, "trade_structure",
            ["trade_id"], ["id"], ondelete="SET NULL",
        )
        op.create_foreign_key(
            f"fk_{table}_package_id", table, "package",
            ["package_id"], ["id"], ondelete="SET NULL",
        )
    op.add_column("trade_order", sa.Column("ib_local_symbol", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("trade_order", "ib_local_symbol")
    for table in reversed(_POS_TABLES):
        op.drop_constraint(f"fk_{table}_package_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_trade_id", table, type_="foreignkey")
        op.drop_column(table, "package_id")
        op.drop_column(table, "trade_id")
        op.drop_column(table, "contract_id")
    op.drop_constraint("fk_trade_structure_package_id", "trade_structure", type_="foreignkey")
    op.drop_column("trade_structure", "package_id")
    op.drop_table("package")
