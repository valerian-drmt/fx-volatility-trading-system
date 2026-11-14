"""idempotency_key: the answer each order request got, by its key (#497).

Only the structured submit was protected against a repeat. A close, a trade
close, a spot order, a manual ticket and a cancel created a new order on
every request. The api now records the ``Idempotency-Key`` of each order
request with its answer and replays the answer on a repeat
(``api/middleware/idempotency.py``).

Revision ID: 072_idempotency_key
Revises: 071_order_state_one_writer
Create Date: 2026-11-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "072_idempotency_key"
down_revision: str | None = "071_order_state_one_writer"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_key",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('in_flight','done')", name="ck_idempotency_key_state"),
    )
    op.create_index("ix_idempotency_key_created_at", "idempotency_key", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_key_created_at", table_name="idempotency_key")
    op.drop_table("idempotency_key")
