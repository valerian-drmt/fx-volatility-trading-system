"""Add IV / Vanna / Volga columns to ``positions`` and ``position_snapshots``.

Implements **priorité 2** of ``risk_dashboard_spec.md`` : enrich panel E
with implied volatility (input) and 2nd-order vol Greeks (vanna, volga).

  iv             : implied vol used during compute (decimal, 0.08 = 8%)
  vanna_usd      : ∂Δ/∂σ × qty × multiplier × 0.01
                   ($ change in delta per 1 vol pt move of IV)
  volga_usd      : ∂²P/∂σ² × qty × multiplier × (0.01)²
                   (P&L convexity per (1 vol pt)² IV move)

Same columns added to ``position_snapshots`` so history mirrors the live
``positions`` row 1:1 (cf. migration 030).

Revision ID: 031_iv_vanna_volga
Revises: 030_snapshots_mirror_positions
Create Date: 2026-05-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "031_iv_vanna_volga"
down_revision: str | None = "030_snapshots_mirror_positions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("positions", "position_snapshots"):
        op.add_column(table, sa.Column("iv", sa.Numeric(8, 5), nullable=True))
        op.add_column(table, sa.Column("vanna_usd", sa.Numeric(15, 2), nullable=True))
        op.add_column(table, sa.Column("volga_usd", sa.Numeric(15, 2), nullable=True))


def downgrade() -> None:
    for table in ("position_snapshots", "positions"):
        op.drop_column(table, "volga_usd")
        op.drop_column(table, "vanna_usd")
        op.drop_column(table, "iv")
