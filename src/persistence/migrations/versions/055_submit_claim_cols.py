"""``submit_claimed_at`` on ``trade_order`` and ``hedge_order`` (#318).

The atomic placement claim existed only on ``trade_structure`` (entry path).
Every other IB placement — hedge, rollback unwind, close, manual — now goes
through ``engines.execution.placement.place_with_claim``, which claims the
ROW it is about to place (``UPDATE ... WHERE submit_claimed_at IS NULL AND
ib_order_id IS NULL``) before touching IB. Two nullable timestamps, additive,
no payload cascade; a NULL on an existing row means "never claimed", which is
the right reading for rows that already carry an ``ib_order_id``.

Revision ID: 055_submit_claim_cols
Revises: 054_spot_order_ib_status
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "055_submit_claim_cols"
down_revision: str | None = "054_spot_order_ib_status"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("trade_order", sa.Column("submit_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("hedge_order", sa.Column("submit_claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("hedge_order", "submit_claimed_at")
    op.drop_column("trade_order", "submit_claimed_at")
