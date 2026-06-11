"""Add live (per-cycle-updated) panel E columns directly on ``positions``.

After migration 025 the table mirrored the *static* panel E columns. This
adds the *live* ones so the API can read everything from a single row :

    market_price     : contract mark (futures price for FUT, option premium for OPT)
    current_pnl_usd  : unrealized P&L (IB-canonical from ``unrealizedPNL``)
    delta_usd        : Δ in $ per unit spot
    gamma_usd        : Γ in $/pip
    vega_usd         : Vega in $/volpt
    theta_usd        : Θ in $/day

Writer = risk-engine (cycle 2 s, single source of truth for greeks).
``position_snapshots`` keeps the same columns for history / audit.

Revision ID: 026_positions_live_cols
Revises: 025_positions_panel_only
Create Date: 2026-05-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "026_positions_live_cols"
down_revision: str | None = "025_positions_panel_only"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("market_price", sa.Numeric(15, 8), nullable=True))
    op.add_column("positions", sa.Column("current_pnl_usd", sa.Numeric(15, 2), nullable=True))
    op.add_column("positions", sa.Column("delta_usd", sa.Numeric(15, 2), nullable=True))
    op.add_column("positions", sa.Column("gamma_usd", sa.Numeric(15, 2), nullable=True))
    op.add_column("positions", sa.Column("vega_usd", sa.Numeric(15, 2), nullable=True))
    op.add_column("positions", sa.Column("theta_usd", sa.Numeric(15, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "theta_usd")
    op.drop_column("positions", "vega_usd")
    op.drop_column("positions", "gamma_usd")
    op.drop_column("positions", "delta_usd")
    op.drop_column("positions", "current_pnl_usd")
    op.drop_column("positions", "market_price")
