"""Theme 4 — fold delta_hedge_config + risk_limits into config_scalar.
Rename ib_connection_state → runtime_ib_session.

R10.1 Theme 4, re-derived direct-to-final on the live 032 head (collapses the
r10 path app_config_scalar→config_scalar / ib_session_state→config_ib_session→
runtime_ib_session into single target names). DeltaHedge + RiskLimit have a
strictly identical scalar shape → legitimate fold into config_scalar
(namespace, name, value, unit, ...). ib_connection_state is runtime state
(UPDATE-in-place singleton) → the runtime_ check constraint name is preserved.

Revision ID: 033_config_scalar_ib_rename
Revises: 032_rename_vol_indicator_tables
Create Date: 2026-06-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "033_config_scalar_ib_rename"
down_revision: str | None = "032_rename_vol_indicator_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "config_scalar",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("namespace", sa.String(40), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20)),
        sa.Column("description", sa.String(300)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(40)),
        sa.UniqueConstraint("namespace", "name", name="uq_config_scalar_ns_name"),
    )
    op.create_index("ix_config_scalar_ns_active", "config_scalar", ["namespace", "is_active"])

    op.execute("""
        INSERT INTO config_scalar
            (namespace, name, value, unit, description, is_active, updated_at)
        SELECT 'delta_hedge', config_name, config_value, unit, description, TRUE, updated_at
          FROM delta_hedge_config
    """)
    op.execute("""
        INSERT INTO config_scalar
            (namespace, name, value, unit, description, is_active, updated_at, updated_by)
        SELECT 'risk', limit_name, limit_value, unit, description, is_active, updated_at, updated_by
          FROM risk_limits
    """)

    op.drop_table("delta_hedge_config")
    op.drop_table("risk_limits")

    op.rename_table("ib_connection_state", "runtime_ib_session")


def downgrade() -> None:
    op.rename_table("runtime_ib_session", "ib_connection_state")

    op.create_table(
        "delta_hedge_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("config_name", sa.String(40), nullable=False, unique=True),
        sa.Column("config_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "risk_limits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("limit_name", sa.String(60), nullable=False, unique=True),
        sa.Column("limit_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(40)),
    )

    op.execute("""
        INSERT INTO delta_hedge_config (config_name, config_value, unit, description, updated_at)
        SELECT name, value, COALESCE(unit, ''), description, updated_at
          FROM config_scalar WHERE namespace = 'delta_hedge'
    """)
    op.execute("""
        INSERT INTO risk_limits (limit_name, limit_value, unit, description, is_active, updated_at, updated_by)
        SELECT name, value, COALESCE(unit, ''), description, is_active, updated_at, updated_by
          FROM config_scalar WHERE namespace = 'risk'
    """)

    op.drop_index("ix_config_scalar_ns_active", table_name="config_scalar")
    op.drop_table("config_scalar")
