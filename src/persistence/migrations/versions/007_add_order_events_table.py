"""Add order_events table — append-only audit log for user → IB actions.

Revision ID: 007_add_order_events_table
Revises: 006_drop_position_exit_cols
Create Date: 2026-04-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_add_order_events_table"
down_revision: str | None = "006_drop_position_exit_cols"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("request_payload", postgresql.JSONB().with_variant(sa.JSON(), "sqlite"), nullable=False),
        sa.Column("response_payload", postgresql.JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "action_type IN ('SUBMIT', 'CANCEL', 'CLOSE_POSITION')",
            name="ck_order_events_action_type",
        ),
    )
    op.create_index("idx_order_events_timestamp", "order_events", [sa.text("timestamp DESC")])
    op.create_index("idx_order_events_order_id", "order_events", ["order_id"])


def downgrade() -> None:
    op.drop_index("idx_order_events_order_id", table_name="order_events")
    op.drop_index("idx_order_events_timestamp", table_name="order_events")
    op.drop_table("order_events")
