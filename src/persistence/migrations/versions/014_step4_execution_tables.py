"""Step 4 — Execution (mock-mode sandbox, no IB wiring yet).

Cf. docs/vol_trading_pca/specs/STEP4_EXECUTION.md §5.

Tables (renamed from spec to avoid clashing with the legacy R8
``orders``/``positions``/``trades`` tables) :
  - trade_structures      : parent of multi-leg trades (1 / Submit)
  - structure_orders      : 1 per leg
  - structure_fills       : 1 per IB execution_id (here : 1 / order in mock)
  - trade_positions       : 1 / fully-filled structure (consumed by Step 5)
  - execution_audit_log   : granular event log

Skipped vs spec : ``ib_connection_state`` (no live IB in sandbox).

Revision ID: 014_step4_execution_tables
Revises: 013_step3_trade_preview_tables
Create Date: 2026-05-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014_step4_execution_tables"
down_revision: str | None = "013_step3_trade_preview_tables"
branch_labels: str | None = None
depends_on: str | None = None

JSONB_PORTABLE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "trade_structures",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("preview_id", sa.String(40), sa.ForeignKey("trade_previews.preview_id"), nullable=True),
        sa.Column("pca_signal_id", sa.BigInteger, sa.ForeignKey("pca_signals.id"), nullable=True),
        sa.Column("triggering_pc", sa.Integer),
        sa.Column("armed_z_score", sa.Numeric(10, 4)),
        sa.Column("armed_signal_label", sa.String(15)),
        sa.Column("structure_type", sa.String(40), nullable=False),
        sa.Column("reference_tenor", sa.String(10), nullable=False),
        sa.Column("expiry_date", sa.Date),
        sa.Column("base_qty", sa.Integer, nullable=False),
        sa.Column("state", sa.String(25), nullable=False),
        sa.Column("state_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ib_combo_order_id", sa.String(40)),
        sa.Column("execution_mode", sa.String(20), nullable=False, server_default="mock"),
        sa.Column("total_premium_paid_usd", sa.Float),
        sa.Column("total_slippage_usd", sa.Float),
        sa.Column("total_commission_usd", sa.Float),
        sa.Column("total_entry_cost_usd", sa.Float),
        sa.Column("first_fill_at", sa.DateTime(timezone=True)),
        sa.Column("fully_filled_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.String(80)),
        sa.CheckConstraint(
            "state IN ('submitted','partial_fill','fully_filled','partial_fail','fully_failed','closed')",
            name="ck_trade_structures_state",
        ),
    )
    op.create_index("ix_trade_structures_state", "trade_structures", ["state", "created_at"])

    op.create_table(
        "structure_orders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("structure_id", sa.BigInteger, sa.ForeignKey("trade_structures.id"), nullable=False),
        sa.Column("leg_idx", sa.Integer, nullable=False),
        sa.Column("ib_order_id", sa.String(40)),
        sa.Column("ib_perm_id", sa.String(40)),
        sa.Column("contract_symbol", sa.String(10), nullable=False, server_default="EUR"),
        sa.Column("contract_type", sa.String(10), nullable=False),
        sa.Column("contract_expiry", sa.Date),
        sa.Column("contract_strike", sa.Float),
        sa.Column("contract_exchange", sa.String(10), nullable=False, server_default="CME"),
        sa.Column("contract_currency", sa.String(5), nullable=False, server_default="USD"),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False, server_default="LMT"),
        sa.Column("limit_price", sa.Float),
        sa.Column("time_in_force", sa.String(5), nullable=False, server_default="DAY"),
        sa.Column("preview_iv_pct", sa.Float),
        sa.Column("preview_price", sa.Float),
        sa.Column("state", sa.String(25), nullable=False),
        sa.Column("state_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_code", sa.String(20)),
        sa.Column("rejection_text", sa.String(300)),
        sa.Column("qty_filled", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Float),
        sa.Column("total_commission_usd", sa.Float, server_default="0"),
        sa.Column("fully_filled_at", sa.DateTime(timezone=True)),
        sa.Column("slippage_per_contract", sa.Float),
        sa.Column("total_slippage_usd", sa.Float),
        sa.UniqueConstraint("structure_id", "leg_idx", name="uq_structure_orders_structure_leg"),
        sa.CheckConstraint(
            "state IN ('pending','submitted','acknowledged','partially_filled','filled','rejected','cancelled','expired')",
            name="ck_structure_orders_state",
        ),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_structure_orders_side"),
    )
    op.create_index("ix_structure_orders_structure", "structure_orders", ["structure_id", "leg_idx"])

    op.create_table(
        "structure_fills",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("order_id", sa.BigInteger, sa.ForeignKey("structure_orders.id"), nullable=False),
        sa.Column("ib_execution_id", sa.String(60), nullable=False, unique=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qty_filled", sa.Integer, nullable=False),
        sa.Column("fill_price", sa.Float, nullable=False),
        sa.Column("commission_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("exchange", sa.String(10)),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("spot_at_fill", sa.Float),
        sa.Column("bid_at_fill", sa.Float),
        sa.Column("ask_at_fill", sa.Float),
        sa.Column("iv_implied_from_fill", sa.Float),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_structure_fills_order", "structure_fills", ["order_id", "timestamp"])

    op.create_table(
        "trade_positions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("structure_id", sa.BigInteger, sa.ForeignKey("trade_structures.id"), nullable=False, unique=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_premium_usd", sa.Float, nullable=False),
        sa.Column("entry_total_cost_usd", sa.Float, nullable=False),
        sa.Column("state", sa.String(15), nullable=False, server_default="open"),
        sa.Column("state_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("entry_vega_usd_per_volpt", sa.Float),
        sa.Column("entry_gamma_usd_per_pip2", sa.Float),
        sa.Column("entry_theta_usd_per_day", sa.Float),
        sa.Column("entry_spot", sa.Float),
        sa.Column("entry_iv_avg", sa.Float),
        sa.Column("entry_regime", sa.String(20)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.String(80)),
        sa.Column("exit_premium_usd", sa.Float),
        sa.Column("exit_total_cost_usd", sa.Float),
        sa.Column("gross_pnl_usd", sa.Float),
        sa.Column("net_pnl_usd", sa.Float),
        sa.CheckConstraint(
            "state IN ('open','closing','closed','expired')",
            name="ck_trade_positions_state",
        ),
    )
    op.create_index("ix_trade_positions_state", "trade_positions", ["state", "opened_at"])

    op.create_table(
        "execution_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("structure_id", sa.BigInteger, sa.ForeignKey("trade_structures.id")),
        sa.Column("order_id", sa.BigInteger, sa.ForeignKey("structure_orders.id")),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(15), nullable=False, server_default="info"),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("payload", JSONB_PORTABLE),
        sa.CheckConstraint(
            "severity IN ('debug','info','warning','error','critical')",
            name="ck_audit_severity",
        ),
    )
    op.create_index("ix_audit_timestamp", "execution_audit_log", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_audit_timestamp", table_name="execution_audit_log")
    op.drop_table("execution_audit_log")
    op.drop_index("ix_trade_positions_state", table_name="trade_positions")
    op.drop_table("trade_positions")
    op.drop_index("ix_structure_fills_order", table_name="structure_fills")
    op.drop_table("structure_fills")
    op.drop_index("ix_structure_orders_structure", table_name="structure_orders")
    op.drop_table("structure_orders")
    op.drop_index("ix_trade_structures_state", table_name="trade_structures")
    op.drop_table("trade_structures")
