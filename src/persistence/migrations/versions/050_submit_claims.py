"""Add submit/rollback claim stamps to trade_structure (EXEC-1 / EXEC-3).

Two nullable timestamps, no constraint edits, no backfill:

1. ``submit_claimed_at`` — atomic idempotency claim taken (and committed)
   BEFORE any order is placed at IB. A replayed live-submit call finds the
   claim set and is refused, so an API retry can never place a second set
   of live orders. Cleared when the submit fails before any placement, so
   a genuine retry stays possible.
2. ``rollback_started_at`` — stamp set by the first rollback pass. Serialises
   concurrent rollback calls and marks re-entries in the audit trail; the
   actual double-unwind safety is the residual-quantity math in
   ``core.execution.rollback``.

Revision ID: 050_submit_claims
Revises: 049_sync_drift
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "050_submit_claims"
down_revision: str | None = "049_sync_drift"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "trade_structure",
        sa.Column("submit_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trade_structure",
        sa.Column("rollback_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_structure", "rollback_started_at")
    op.drop_column("trade_structure", "submit_claimed_at")
