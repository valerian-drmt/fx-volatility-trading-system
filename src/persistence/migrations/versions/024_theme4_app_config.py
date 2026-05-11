"""Theme 4 — fold delta_hedge_config + risk_limits into app_config_scalar.
Rename ib_connection_state → ib_session_state.

Variante mini-B ciblée (cf. docs/db-schema-theme4-plan.md) :
  - DeltaHedge + RiskLimit ont shape strictement identique → fold légitime
    dans une table `app_config_scalar(namespace, name, value, unit, ...)`.
  - IbConnectionState rename pour clarifier semantics (= state runtime,
    pas connection figée).
  - VolConfig, ExitRulesConfig, ExitAlert intacts (shape spécialisée
    légitime — voir plan § Out of scope).

Revision ID: 024_theme4_app_config
Revises: 023_theme1_renames
Create Date: 2026-05-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "024_theme4_app_config"
down_revision: str | None = "023_theme1_renames"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Create unified scalar config table.
    op.create_table(
        "app_config_scalar",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("namespace", sa.String(40), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20)),
        sa.Column("description", sa.String(300)),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.String(40)),
        sa.UniqueConstraint("namespace", "name", name="uq_app_config_scalar_ns_name"),
    )
    op.create_index(
        "ix_app_config_scalar_ns_active",
        "app_config_scalar",
        ["namespace", "is_active"],
    )

    # 2. Copy delta_hedge_config rows with namespace='delta_hedge'.
    op.execute("""
        INSERT INTO app_config_scalar
            (namespace, name, value, unit, description, is_active, updated_at)
        SELECT 'delta_hedge', config_name, config_value, unit, description, TRUE, updated_at
          FROM delta_hedge_config
    """)

    # 3. Copy risk_limits rows with namespace='risk'.
    op.execute("""
        INSERT INTO app_config_scalar
            (namespace, name, value, unit, description, is_active, updated_at, updated_by)
        SELECT 'risk', limit_name, limit_value, unit, description, is_active,
               updated_at, updated_by
          FROM risk_limits
    """)

    # 4. Drop old tables now that data is migrated.
    op.drop_table("delta_hedge_config")
    op.drop_table("risk_limits")

    # 5. Rename ib_connection_state → ib_session_state.
    op.rename_table("ib_connection_state", "ib_session_state")


def downgrade() -> None:
    # 1. Reverse rename.
    op.rename_table("ib_session_state", "ib_connection_state")

    # 2. Recreate delta_hedge_config + risk_limits (schemas from migrations 013/016).
    op.create_table(
        "delta_hedge_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("config_name", sa.String(40), nullable=False, unique=True),
        sa.Column("config_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20)),
        sa.Column("description", sa.String(300)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "risk_limits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("limit_name", sa.String(60), nullable=False, unique=True),
        sa.Column("limit_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20)),
        sa.Column("description", sa.String(300)),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.String(40)),
    )

    # 3. Copy back.
    op.execute("""
        INSERT INTO delta_hedge_config (config_name, config_value, unit, description, updated_at)
        SELECT name, value, unit, description, updated_at
          FROM app_config_scalar WHERE namespace = 'delta_hedge'
    """)
    op.execute("""
        INSERT INTO risk_limits
            (limit_name, limit_value, unit, description, is_active, updated_at, updated_by)
        SELECT name, value, unit, description, is_active, updated_at, updated_by
          FROM app_config_scalar WHERE namespace = 'risk'
    """)

    # 4. Drop unified table.
    op.drop_index("ix_app_config_scalar_ns_active", table_name="app_config_scalar")
    op.drop_table("app_config_scalar")
