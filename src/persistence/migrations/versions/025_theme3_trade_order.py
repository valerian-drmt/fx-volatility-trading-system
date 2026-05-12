"""Theme 3 — Trade/Order schema refactor.

Variante B pragmatique (cf. docs/db-schema-theme3-plan.md):
  - DROP 3 dead tables: trades, orders, order_events (zero R9 writers).
  - RENAME 7 tables to naming conventions:
      trade_previews        → trade_preview
      trade_structures      → trade_structure
      structure_orders      → trade_order
      structure_fills       → trade_fill
      structure_definitions → structure_definition_ref
      hedge_orders          → hedge_order
      exit_alerts           → exit_alert
  - FOLD execution_audit_log → trade_event (event_type='audit', extensible).

`order` would have been the canonical singular but it's a Postgres reserved
word — chose `trade_order` to keep raw SQL unquoted.

Revision ID: 025_theme3_trade_order
Revises: 024_theme4_app_config
Create Date: 2026-05-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "025_theme3_trade_order"
down_revision: str | None = "024_theme4_app_config"
branch_labels: str | None = None
depends_on: str | None = None


_RENAMES: list[tuple[str, str]] = [
    ("trade_previews", "trade_preview"),
    ("trade_structures", "trade_structure"),
    ("structure_orders", "trade_order"),
    ("structure_fills", "trade_fill"),
    ("structure_definitions", "structure_definition_ref"),
    ("hedge_orders", "hedge_order"),
    ("exit_alerts", "exit_alert"),
]


def upgrade() -> None:
    # 1. Drop dead tables (no live writers in R9).
    #    CASCADE handles the FK from order_events → orders (both dropped).
    op.execute("DROP TABLE IF EXISTS order_events CASCADE")
    op.execute("DROP TABLE IF EXISTS orders CASCADE")
    op.execute("DROP TABLE IF EXISTS trades CASCADE")

    # 2. Rename 7 tables. Postgres tracks FKs by OID, so existing FKs
    #    targeting these tables stay valid automatically.
    for old, new in _RENAMES:
        op.rename_table(old, new)

    # 3. Create unified trade_event table (event journal, append-only).
    op.create_table(
        "trade_event",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column(
            "severity", sa.String(15),
            nullable=False, server_default=sa.text("'info'"),
        ),
        sa.Column(
            "structure_id", sa.BigInteger,
            sa.ForeignKey("trade_structure.id"),
        ),
        sa.Column(
            "order_id", sa.BigInteger,
            sa.ForeignKey("trade_order.id"),
        ),
        sa.Column(
            "position_id", sa.BigInteger,
            sa.ForeignKey("trade_positions.id"),
        ),
        sa.Column("description", sa.String(500)),
        sa.Column(
            "payload", sa.JSON,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "severity IN ('debug','info','warning','error','critical')",
            name="ck_trade_event_severity",
        ),
    )
    op.create_index("ix_trade_event_ts", "trade_event", [sa.text("ts DESC")])
    op.create_index(
        "ix_trade_event_structure",
        "trade_event", ["structure_id"],
        postgresql_where=sa.text("structure_id IS NOT NULL"),
    )
    op.create_index(
        "ix_trade_event_type_ts",
        "trade_event", ["event_type", sa.text("ts DESC")],
    )

    # 4. Copy execution_audit_log rows → trade_event preserving event_type.
    #    Column mapping:
    #      timestamp    → ts
    #      event_type   → event_type (as-is: 'structure_filled', 'submit_failed', …)
    #      severity     → severity
    #      structure_id → structure_id
    #      order_id     → order_id
    #      message      → description
    #      payload      → payload
    op.execute("""
        INSERT INTO trade_event
            (ts, event_type, severity, structure_id, order_id, description, payload)
        SELECT
            timestamp, event_type, severity,
            structure_id, order_id, message,
            COALESCE(payload, '{}'::jsonb)
          FROM execution_audit_log
    """)

    # 5. Drop execution_audit_log now that data is migrated.
    op.drop_table("execution_audit_log")


def downgrade() -> None:
    # 1. Recreate execution_audit_log (schema from migration 014).
    op.create_table(
        "execution_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "structure_id", sa.BigInteger,
            sa.ForeignKey("trade_structure.id"),
        ),
        sa.Column(
            "order_id", sa.BigInteger,
            sa.ForeignKey("trade_order.id"),
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column(
            "severity", sa.String(15),
            nullable=False, server_default=sa.text("'info'"),
        ),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("payload", sa.JSON),
        sa.CheckConstraint(
            "severity IN ('debug','info','warning','error','critical')",
            name="ck_audit_severity",
        ),
    )

    # 2. Copy events back (all rows — execution_audit_log was the only source).
    op.execute("""
        INSERT INTO execution_audit_log
            (timestamp, structure_id, order_id, event_type, severity, message, payload)
        SELECT
            ts, structure_id, order_id, event_type, severity,
            COALESCE(description, ''), payload
          FROM trade_event
    """)

    # 3. Drop trade_event.
    op.drop_index("ix_trade_event_type_ts", table_name="trade_event")
    op.drop_index("ix_trade_event_structure", table_name="trade_event")
    op.drop_index("ix_trade_event_ts", table_name="trade_event")
    op.drop_table("trade_event")

    # 4. Reverse the 7 renames (in reverse order to be safe).
    for old, new in reversed(_RENAMES):
        op.rename_table(new, old)

    # 5. Recreate dead tables (empty — they had no R9 data).
    #    Minimal schema sufficient for downgrade reversibility.
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("position_id", sa.Integer),
        sa.Column("ib_order_id", sa.String(50)),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
        sa.Column("price", sa.Numeric(15, 8), nullable=False),
        sa.Column("commission", sa.Numeric(10, 4)),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spot_at_execution", sa.Numeric(15, 8)),
        sa.Column("iv_at_execution", sa.Numeric(8, 5)),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ib_perm_id", sa.BigInteger),
        sa.Column("ib_order_id", sa.Integer, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("sec_type", sa.String(10), nullable=False),
        sa.Column("expiry", sa.String(10)),
        sa.Column("strike", sa.Numeric(10, 5)),
        sa.Column("right", sa.String(2)),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
        sa.Column("limit_price", sa.Numeric(15, 8)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("filled_qty", sa.Numeric(15, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("avg_fill_price", sa.Numeric(15, 8)),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id")),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("request_payload", sa.JSON, nullable=False),
        sa.Column("response_payload", sa.JSON),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.String(500)),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
