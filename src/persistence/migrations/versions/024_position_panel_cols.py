"""Add panel-E-shaped columns to positions and position_snapshots.

Goal : the DB schema mirrors what the Portfolio Panel section E displays,
so the API serializer becomes a thin pass-through (no on-the-fly compute).

Static columns on ``positions`` (set once at IB sync) :
  - local_symbol       : IB ``localSymbol`` like "6EM6", "M6EM6", "EUUN6 P1170"
  - multiplier         : 125000 (EUR std) / 12500 (M6E mini) / etc.
  - nominal_eur        : |qty| × multiplier (underlying volume controlled)
  - contract_price_entry : entry_price / multiplier (unit price at entry)

Live column on ``position_snapshots`` (set each cycle by risk-engine) :
  - market_price       : the contract's current mark — futures price for
                         FUT, option premium per unit for OPT. Comes from
                         IB ``updatePortfolio.marketPrice``.

Revision ID: 024_position_panel_cols
Revises: 023_trade_positions_ib_recon
Create Date: 2026-05-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "024_position_panel_cols"
down_revision: str | None = "023_trade_positions_ib_recon"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("local_symbol", sa.String(20), nullable=True))
    op.add_column("positions", sa.Column("multiplier", sa.Numeric(10, 2), nullable=True))
    op.add_column("positions", sa.Column("nominal_eur", sa.Numeric(15, 2), nullable=True))
    op.add_column(
        "positions",
        sa.Column("contract_price_entry", sa.Numeric(15, 8), nullable=True),
    )
    op.add_column(
        "position_snapshots",
        sa.Column("market_price", sa.Numeric(15, 8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("position_snapshots", "market_price")
    op.drop_column("positions", "contract_price_entry")
    op.drop_column("positions", "nominal_eur")
    op.drop_column("positions", "multiplier")
    op.drop_column("positions", "local_symbol")
