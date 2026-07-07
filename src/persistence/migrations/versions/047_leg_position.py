"""OMS P1/P2 — forward per-leg position (the book) + closing-order link.

Create ``leg_position`` : one row per ``trade_order`` (leg), ``open_qty`` a pure
signed fold of that leg's fills (I3/I7), plus ``reserved_qty`` for the P2
reservation ledger (I5). Add ``trade_order.closes_order_id`` so a closing order
points at the entry leg it closes (reservation attribution, I5).

Additive : a new table + one nullable column. No existing writer/reader changes
shape; ``leg_position`` is rebuilt from ``trade_fill`` by
``persistence.projection`` (idempotent), so no data backfill is needed here.

Revision ID: 047_leg_position
Revises: 046_trace_id
Create Date: 2026-07-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "047_leg_position"
down_revision: str | None = "046_trace_id"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "leg_position",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "order_id", sa.BigInteger(),
            sa.ForeignKey("trade_order.id"), nullable=False, unique=True,
        ),
        sa.Column("open_qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("reserved_qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Numeric(15, 8), nullable=True),
        sa.Column("realized_pnl_usd", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column(
            "rebuilt_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "trade_order",
        sa.Column(
            "closes_order_id", sa.BigInteger(),
            sa.ForeignKey("trade_order.id"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("trade_order", "closes_order_id")
    op.drop_table("leg_position")
