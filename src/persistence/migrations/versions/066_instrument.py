"""instrument (one row per IB contract id) and trade_order.con_id (#460).

Until now the only identity of a contract was its IB local symbol, and one
check rebuilt that string from a preview's strike and date. ``instrument``
keeps what the broker says about each contract (type, multiplier, listed
expiry, strike); ``trade_order.con_id`` records which contract a leg really
filled. The engine fills the table within one position sync; the backfill
here covers the contracts held at migration time.

Revision ID: 066_instrument
Revises: 065_position_event
Create Date: 2026-11-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "066_instrument"
down_revision: str | None = "065_position_event"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "instrument",
        sa.Column("con_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("local_symbol", sa.String(20), nullable=False),
        sa.Column("sec_type", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(10), nullable=True),
        sa.Column("currency", sa.String(5), nullable=True),
        sa.Column("exchange", sa.String(12), nullable=True),
        sa.Column("multiplier", sa.Numeric(15, 4), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(15, 8), nullable=True),
        sa.Column("option_right", sa.String(1), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_instrument_local_symbol", "instrument", ["local_symbol"])
    op.add_column("trade_order", sa.Column("con_id", sa.BigInteger(), nullable=True))
    # Legs on contracts held right now: the mirror already has their contract id.
    op.execute(
        "UPDATE trade_order SET con_id = ("
        "  SELECT op.contract_id FROM open_position op"
        "  WHERE op.structure = trade_order.ib_local_symbol AND op.contract_id IS NOT NULL"
        "  LIMIT 1"
        ") WHERE con_id IS NULL AND ib_local_symbol IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("trade_order", "con_id")
    op.drop_index("ix_instrument_local_symbol", table_name="instrument")
    op.drop_table("instrument")
