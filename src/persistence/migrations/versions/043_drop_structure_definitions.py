"""Drop the structure_definitions catalogue mirror table.

R10.2 — the structure catalogue was a 6-row static seed mirroring the templates
in ``core.trade_preview.TEMPLATES``. The /api/v1/trade/structures endpoint now
reads the in_catalog=True entries of TEMPLATES directly (single source of
truth). Downgrade recreates the table (no re-seed).

Revision ID: 043_drop_structure_definitions
Revises: 042_drop_lookup_mirrors
Create Date: 2026-06-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "043_drop_structure_definitions"
down_revision: str | None = "042_drop_lookup_mirrors"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("structure_definitions")


def downgrade() -> None:
    op.create_table(
        "structure_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("structure_type", sa.String(40), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("leg_template", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("min_legs", sa.Integer, nullable=False),
        sa.Column("max_legs", sa.Integer, nullable=False),
        sa.Column("requires_delta_hedge", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("typical_vega_sign", sa.String(10), nullable=False),
        sa.Column("typical_gamma_sign", sa.String(10), nullable=False),
        sa.Column("typical_theta_sign", sa.String(10), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("rationale_for_pc", sa.String(300)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
    )
