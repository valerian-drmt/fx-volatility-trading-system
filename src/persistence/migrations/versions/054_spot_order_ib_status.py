"""``spot_order.ib_status`` + ``why_held`` — why a spot order waits (#376).

The blotter showed every working spot order as "waiting" and a refused one as
a bare "cancelled": the row kept neither IB's own status (PreSubmitted vs
Submitted) nor its hint (``whyHeld``) nor its error text. Two nullable
columns, additive, no payload cascade.

Revision ID: 054_spot_order_ib_status
Revises: 053_uq_structure_preview
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "054_spot_order_ib_status"
down_revision: str | None = "053_uq_structure_preview"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("spot_order", sa.Column("ib_status", sa.String(20), nullable=True))
    op.add_column("spot_order", sa.Column("why_held", sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column("spot_order", "why_held")
    op.drop_column("spot_order", "ib_status")
