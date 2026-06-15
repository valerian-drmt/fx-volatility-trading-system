"""Fold ``execution_audit_log`` into the unified ``trade_event`` journal.

R10.2 — completes the R10.1-deferred Theme-3 fold (the source table is heavily
written, so it was held back until the feature wave). Rename-based (data
preserved) : table + columns renamed in place, ``position_id`` added, the
append-only journal indexes created, severity check-constraint renamed to match.

  execution_audit_log → trade_event
  timestamp           → ts
  message (NOT NULL)  → description (nullable)
  + position_id  FK booked_position
  + ix_trade_event_ts / _structure / _type_ts
  ck_audit_severity   → ck_trade_event_severity

Revision ID: 038_fold_trade_event
Revises: 037_drop_vrp_default_curve
Create Date: 2026-06-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "038_fold_trade_event"
down_revision: str | None = "037_drop_vrp_default_curve"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.rename_table("execution_audit_log", "trade_event")
    op.alter_column("trade_event", "timestamp", new_column_name="ts")
    op.alter_column(
        "trade_event", "message",
        new_column_name="description",
        existing_type=sa.String(500), nullable=True,
    )
    op.add_column(
        "trade_event",
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("booked_position.id")),
    )
    op.execute("ALTER TABLE trade_event RENAME CONSTRAINT ck_audit_severity TO ck_trade_event_severity")
    op.execute("UPDATE trade_event SET payload = '{}'::jsonb WHERE payload IS NULL")
    op.alter_column(
        "trade_event", "payload",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False, server_default=sa.text("'{}'::jsonb"),
    )
    op.create_index("ix_trade_event_ts", "trade_event", [sa.text("ts DESC")])
    op.create_index(
        "ix_trade_event_structure", "trade_event", ["structure_id"],
        postgresql_where=sa.text("structure_id IS NOT NULL"),
    )
    op.create_index("ix_trade_event_type_ts", "trade_event", ["event_type", sa.text("ts DESC")])


def downgrade() -> None:
    op.drop_index("ix_trade_event_type_ts", table_name="trade_event")
    op.drop_index("ix_trade_event_structure", table_name="trade_event")
    op.drop_index("ix_trade_event_ts", table_name="trade_event")
    op.alter_column(
        "trade_event", "payload",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=True, server_default=None,
    )
    op.execute("ALTER TABLE trade_event RENAME CONSTRAINT ck_trade_event_severity TO ck_audit_severity")
    op.drop_column("trade_event", "position_id")
    op.execute("UPDATE trade_event SET description = '' WHERE description IS NULL")
    op.alter_column(
        "trade_event", "description",
        new_column_name="message",
        existing_type=sa.String(500), nullable=False,
    )
    op.alter_column("trade_event", "ts", new_column_name="timestamp")
    op.rename_table("trade_event", "execution_audit_log")
