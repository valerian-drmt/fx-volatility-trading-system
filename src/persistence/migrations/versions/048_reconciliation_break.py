"""Materialised book↔broker breaks (OMS refactor P1, invariant I4).

``reconciliation_break`` : one row per divergence between the book
(Σ leg_position per contract) and the IB mirror (net per contract). At most
one open row (resolved_at IS NULL) per contract ; resolution is stamped,
re-breaks open new rows. Written only by engines.execution.reconciler.

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
        sa.Column("local_symbol", sa.String(20), nullable=False),
        sa.Column("book_qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("broker_qty", sa.Numeric(15, 4), nullable=False),
        sa.Column("diff", sa.Numeric(15, 4), nullable=False),
        sa.Column("break_type", sa.String(20), nullable=False),
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
    # The reconciler's working set : the open break per contract.
    op.create_index(
        "idx_reconciliation_break_open",
        "reconciliation_break",
        ["local_symbol"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_reconciliation_break_open", table_name="reconciliation_break",
    )
    op.drop_table("reconciliation_break")
