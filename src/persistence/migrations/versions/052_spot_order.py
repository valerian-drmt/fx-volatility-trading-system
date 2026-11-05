"""Create ``spot_order`` — persisted spot FX (CASH) orders from the Trade tab.

Spot EUR.USD orders placed from the cash ticket went straight to IB and were
never persisted : invisible in the Orders blotter, lost on refresh, no state
after the "Sent" line and no cancel path (issue #291). They now get their own
table — deliberately NOT a trade_structure/trade_order pair, so cash management
stays out of the structure machinery (booking, reaper, reservation, sync).

Additive (new table + one index), no writer/payload cascade.

Revision ID: 052_spot_order
Revises: 051_acct_ts_idx_dedup
Create Date: 2026-11-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "052_spot_order"
down_revision: str | None = "051_acct_ts_idx_dedup"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "spot_order",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("symbol", sa.String(10), nullable=False, server_default="EUR"),
        sa.Column("currency", sa.String(5), nullable=False, server_default="USD"),
        sa.Column("exchange", sa.String(10), nullable=False, server_default="IDEALPRO"),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False, server_default="LMT"),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("ib_order_id", sa.String(40), nullable=True),
        sa.Column("ib_perm_id", sa.String(40), nullable=True),
        sa.Column("ib_local_symbol", sa.String(20), nullable=True),
        sa.Column("state", sa.String(25), nullable=False, server_default="pending"),
        sa.Column("state_updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("qty_filled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Float(), nullable=True),
        sa.Column("commission_usd", sa.Float(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_text", sa.String(300), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
    )
    op.create_index("ix_spot_order_created_at", "spot_order", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_spot_order_created_at", table_name="spot_order")
    op.drop_table("spot_order")
