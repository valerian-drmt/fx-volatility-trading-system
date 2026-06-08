"""Step 4 phase-2 extras — order_role on structure_orders + ib_connection_state.

Cf. docs/vol_trading_pca/specs/STEP4_EXECUTION.md §5.6 (ib_connection_state)
and STEP5 §7.4 which adds order_role for closing orders.

  - ALTER structure_orders ADD order_role  (default 'entry', constrained)
  - CREATE ib_connection_state             (singleton broker heartbeat row)

The ib_connection_state row is left empty here ; the heartbeat loop in
the execution-engine populates it once a real IB connection exists.

Revision ID: 015_step4_phase2_extras
Revises: 014_step4_execution_tables
Create Date: 2026-05-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "015_step4_phase2_extras"
down_revision: str | None = "014_step4_execution_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. order_role on structure_orders
    op.add_column(
        "structure_orders",
        sa.Column("order_role", sa.String(20), nullable=False, server_default="entry"),
    )
    op.create_check_constraint(
        "ck_structure_orders_order_role",
        "structure_orders",
        "order_role IN ('entry','closing','unwind','hedge')",
    )

    # 2. ib_connection_state singleton
    op.create_table(
        "ib_connection_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("broker", sa.String(20), nullable=False, unique=True, server_default="IB"),
        sa.Column("is_connected", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("account_id", sa.String(40)),
        sa.Column("account_type", sa.String(20)),
        sa.Column("available_funds_usd", sa.Float),
        sa.Column("buying_power_usd", sa.Float),
        sa.Column("margin_used_usd", sa.Float),
        sa.Column("gateway_version", sa.String(40)),
        sa.Column("api_version", sa.String(40)),
        sa.Column("last_disconnect_at", sa.DateTime(timezone=True)),
        sa.Column("n_disconnects_24h", sa.Integer, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "account_type IS NULL OR account_type IN ('paper','live')",
            name="ck_ib_connection_account_type",
        ),
    )

    # Seed singleton row (broker='IB', is_connected=false). The execution-engine
    # heartbeat will UPDATE it ; we never INSERT another row.
    op.execute(
        "INSERT INTO ib_connection_state (broker, is_connected, last_heartbeat) "
        "VALUES ('IB', false, NOW())"
    )


def downgrade() -> None:
    op.drop_table("ib_connection_state")
    op.drop_constraint("ck_structure_orders_order_role", "structure_orders", type_="check")
    op.drop_column("structure_orders", "order_role")
