"""Add ``trace_id`` correlation id to the trade rows.

One id follows a trade end-to-end (API request → exec-engine → async fills), so
its whole story is a single ``grep <trace_id>``. Stamped on ``trade_structure``
at Submit/Close, denormalised onto ``trade_order`` / ``trade_fill`` so the async
fill callbacks (which load the order, not the structure) can re-bind it.

Additive, nullable, no index → no writer/reader/payload cascade, no backfill.

Revision ID: 046_trace_id
Revises: 045_signal_surface_idx
Create Date: 2026-07-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "046_trace_id"
down_revision: str | None = "045_signal_surface_idx"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = ("trade_structure", "trade_order", "trade_fill")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("trace_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "trace_id")
