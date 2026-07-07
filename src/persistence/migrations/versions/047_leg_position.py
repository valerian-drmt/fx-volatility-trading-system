"""Forward position projection (OMS refactor P1, invariants I3/I5/I7).

Two additive changes, no data rewrite :

  * ``leg_position`` — the BOOK position of one leg : one row per entry
    ``trade_order``, open_qty = signed fold of that leg's own fills (via FK),
    plus the ``reserved_qty`` close-reservation ledger (spec §8). Single
    writer : ``engines.execution.position_projector``.
  * ``trade_order.closes_order_id`` — a closing order points at the entry
    order (= the leg) it reduces, stamped at close creation. Forward
    attribution ; never reconstructed from the netted mirror.

Revision ID: 047_leg_position
Revises: 046_signal_read_indexes
Create Date: 2026-07-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "047_leg_position"
down_revision: str | None = "046_signal_read_indexes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "leg_position",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "order_id", sa.BigInteger(),
            sa.ForeignKey("trade_order.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("open_qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("reserved_qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Float(), nullable=True),
        sa.Column("realized_pnl_usd", sa.Float(), nullable=True),
        sa.Column(
            "rebuilt_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "trade_order",
        sa.Column(
            "closes_order_id", sa.BigInteger(),
            sa.ForeignKey(
                "trade_order.id",
                name="fk_trade_order_closes_order_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_trade_order_closes_order_id", "trade_order", ["closes_order_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_trade_order_closes_order_id", table_name="trade_order")
    op.drop_constraint(
        "fk_trade_order_closes_order_id", "trade_order", type_="foreignkey",
    )
    op.drop_column("trade_order", "closes_order_id")
    op.drop_table("leg_position")
