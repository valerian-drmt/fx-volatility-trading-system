"""SVI + SSVI parameter storage tables (Phase P2.1 + P2.2)

Revision ID: 003_svi_ssvi_params
Revises: 254fc54bb36f
Create Date: 2026-04-22 18:30:00.000000+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003_svi_ssvi_params"
down_revision: str | None = "254fc54bb36f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "svi_params",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("underlying", sa.String(20), nullable=False),
        sa.Column("tenor", sa.String(5), nullable=False),
        sa.Column("a", sa.Numeric(10, 7), nullable=False),
        sa.Column("b", sa.Numeric(10, 7), nullable=False),
        sa.Column("rho", sa.Numeric(10, 7), nullable=False),
        sa.Column("m", sa.Numeric(10, 7), nullable=False),
        sa.Column("sigma", sa.Numeric(10, 7), nullable=False),
        sa.Column("rmse_fit", sa.Numeric(10, 7)),
        sa.Column("butterfly_g_min", sa.Numeric(10, 7)),
        sa.UniqueConstraint(
            "timestamp", "underlying", "tenor",
            name="uq_svi_params_ts_underlying_tenor",
        ),
    )
    op.create_index(
        "idx_svi_params_underlying_tenor_ts", "svi_params",
        ["underlying", "tenor", sa.text("timestamp DESC")],
    )

    op.create_table(
        "ssvi_params",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("underlying", sa.String(20), nullable=False),
        sa.Column("spot", sa.Numeric(15, 8), nullable=False),
        sa.Column("eta", sa.Numeric(10, 7), nullable=False),
        sa.Column("gamma", sa.Numeric(10, 7), nullable=False),
        sa.Column("rho", sa.Numeric(10, 7), nullable=False),
        sa.Column("rmse_fit", sa.Numeric(10, 7)),
        sa.Column("calendar_arb_free", sa.Boolean()),
        sa.UniqueConstraint(
            "timestamp", "underlying", name="uq_ssvi_params_ts_underlying",
        ),
    )
    op.create_index(
        "idx_ssvi_params_underlying_ts", "ssvi_params",
        ["underlying", sa.text("timestamp DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_ssvi_params_underlying_ts", table_name="ssvi_params")
    op.drop_table("ssvi_params")
    op.drop_index("idx_svi_params_underlying_tenor_ts", table_name="svi_params")
    op.drop_table("svi_params")
