"""position_event: book changes that are not executions (#463).

A contract that expires leaves IB without a fill, so the leg kept its
quantity for ever. An expiry, an exercise, an assignment or a repair is now
one row here, folded with the fills by ``persistence.projection``.

Revision ID: 065_position_event
Revises: 064_reader_role
Create Date: 2026-11-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "065_position_event"
down_revision: str | None = "064_reader_role"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "position_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("trade_order.id"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("price", sa.Numeric(15, 8), nullable=False, server_default="0"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dedup_key", sa.String(60), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ('expiry','exercise','assignment','repair')", name="ck_position_event_kind",
        ),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_position_event_side"),
        sa.UniqueConstraint("order_id", "kind", "dedup_key", name="uq_position_event_dedup"),
    )


def downgrade() -> None:
    op.drop_table("position_event")
