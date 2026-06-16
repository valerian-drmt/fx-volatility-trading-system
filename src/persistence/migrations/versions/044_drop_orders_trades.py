"""Drop the legacy orders / trades / order_events tables.

R10.2 (final) — the legacy IB order/fill DB-sync flow (sync_orders_from_ib /
sync_trades_from_ib in position_sync) is removed. IB order lifecycle is handled
in trade_order by the execution engine ; fills land in trade_fill ; order-action
audit lands in the trade_event journal. These 3 mirror tables had no remaining
reader. Downgrade recreates them (no re-seed).

Revision ID: 044_drop_orders_trades
Revises: 043_drop_structure_definitions
Create Date: 2026-06-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "044_drop_orders_trades"
down_revision: str | None = "043_drop_structure_definitions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("order_events")   # FK → orders, drop first
    op.drop_table("orders")
    op.drop_table("trades")


def downgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("position_id", sa.Integer, sa.ForeignKey("open_position.id")),
        sa.Column("ib_order_id", sa.String(50)),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
        sa.Column("price", sa.Numeric(15, 8), nullable=False),
        sa.Column("commission", sa.Numeric(10, 4)),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spot_at_execution", sa.Numeric(15, 8)),
        sa.Column("iv_at_execution", sa.Numeric(8, 5)),
        sa.UniqueConstraint("ib_order_id", name="uq_trades_ib_order_id"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_trades_side"),
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
        sa.Column("filled_qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Numeric(15, 8)),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ib_perm_id", name="uq_orders_ib_perm_id"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_orders_side"),
        sa.CheckConstraint(
            "sec_type IN ('FUT', 'FOP', 'STK', 'OPT', 'CONTFUT')", name="ck_orders_sec_type",
        ),
    )
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id")),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.String(500)),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "action_type IN ('SUBMIT', 'CANCEL', 'CLOSE_POSITION')", name="ck_order_events_action_type",
        ),
    )
