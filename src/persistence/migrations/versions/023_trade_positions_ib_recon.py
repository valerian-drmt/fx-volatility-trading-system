"""Add IB reconciliation columns on trade_positions.

Wire-up between local bookings (`trade_positions`, Step 5) and IB sync
(`positions`, execution-engine). At each `position_sync` cycle we now
match each leg of an open `trade_position` to the IB rows by the tuple
``(symbol, instrument_type, strike, maturity, option_type)`` and record
the result.

The frontend Step 5 surfaces the freshness via a coloured badge :
  - fresh   : last reconcile < 5 min
  - stale   : 5 min ≤ last reconcile < 1 h
  - missing : ≥ 1 h or never reconciled (typical when IB Gateway is down)

Revision ID: 023_trade_positions_ib_recon
Revises: 022_purge_market_closed_rows
Create Date: 2026-05-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "023_trade_positions_ib_recon"
down_revision: str | None = "022_purge_market_closed_rows"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "trade_positions",
        sa.Column("ib_reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trade_positions",
        sa.Column("ib_qty_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "trade_positions",
        sa.Column("ib_qty_diff", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_trade_positions_ib_reconciled_at",
        "trade_positions",
        [sa.text("ib_reconciled_at DESC NULLS LAST")],
    )


def downgrade() -> None:
    op.drop_index("ix_trade_positions_ib_reconciled_at", table_name="trade_positions")
    op.drop_column("trade_positions", "ib_qty_diff")
    op.drop_column("trade_positions", "ib_qty_total")
    op.drop_column("trade_positions", "ib_reconciled_at")
