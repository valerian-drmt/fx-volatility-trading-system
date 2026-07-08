"""OMS P1 — materialise book vs broker reconciliation breaks (invariant I4).

New ``reconciliation_break`` table: one open row per contract (``resolved_at IS
NULL``) whenever Σ ``leg_position.open_qty`` disagrees with the netted IB mirror,
classified missing_at_ib / unbooked_at_ib / direction / quantity. Written by
``engines.execution.reconciler``; a break is data that lives and resolves, never
a silent discrepancy.

Additive: a single new table, no change to existing tables.

Revision ID: 048_reconciliation_break
Revises: 047_leg_position
Create Date: 2026-07-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "048_reconciliation_break"
down_revision: str | None = "047_leg_position"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_break",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("local_symbol", sa.String(length=20), nullable=False),
        sa.Column("book_qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("broker_qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("diff", sa.Numeric(15, 4), nullable=False),
        sa.Column("break_type", sa.String(length=20), nullable=False),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "break_type IN ('missing_at_ib','unbooked_at_ib','direction','quantity')",
            name="ck_reconciliation_break_type",
        ),
    )
    # Fast lookup of the current open break per contract.
    op.create_index(
        "ix_reconciliation_break_open",
        "reconciliation_break",
        ["local_symbol"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_reconciliation_break_open", table_name="reconciliation_break")
    op.drop_table("reconciliation_break")
