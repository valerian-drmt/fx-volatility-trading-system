"""Step 5 — Position monitoring tables (Active Positions panel).

Cf. docs/vol_trading_pca/specs/STEP5_ACTIVE_POSITIONS.md §7.

Tables :
  - position_mtm_history       : 1 row / cycle / open position (P&L attribution)
  - position_signal_tracking   : signal-vs-entry comparison, 1 row / cycle
  - hedge_orders               : delta-rebalancing orders (futures)
  - exit_alerts                : 1 row per exit-rule trigger
  - exit_rules_config          : hot-reloadable rule params (5 rows seed)
  - delta_hedge_config         : hot-reloadable hedge params (4 rows seed)

Revision ID: 016_step5_position_monitoring
Revises: 015_step4_phase2_extras
Create Date: 2026-05-03
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "016_step5_position_monitoring"
down_revision: str | None = "015_step4_phase2_extras"
branch_labels: str | None = None
depends_on: str | None = None

JSONB_PORTABLE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # ────────────────────────────────────────────────────────────────
    # 1. position_mtm_history
    op.create_table(
        "position_mtm_history",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("trade_positions.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spot", sa.Float, nullable=False),
        sa.Column("iv_avg_legs_pct", sa.Float),
        sa.Column("current_pnl_gross_usd", sa.Float, nullable=False),
        sa.Column("current_pnl_net_usd", sa.Float, nullable=False),
        sa.Column("vega_pnl_usd", sa.Float),
        sa.Column("gamma_pnl_usd", sa.Float),
        sa.Column("theta_pnl_usd", sa.Float),
        sa.Column("other_pnl_usd", sa.Float),
        sa.Column("current_vega_usd_per_volpt", sa.Float),
        sa.Column("current_gamma_usd_per_pip2", sa.Float),
        sa.Column("current_theta_usd_per_day", sa.Float),
        sa.Column("current_delta_unhedged", sa.Float),
        sa.UniqueConstraint("position_id", "timestamp", name="uq_mtm_position_ts"),
    )
    op.create_index("ix_mtm_position_ts", "position_mtm_history", ["position_id", "timestamp"])

    # ────────────────────────────────────────────────────────────────
    # 2. position_signal_tracking
    op.create_table(
        "position_signal_tracking",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("trade_positions.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triggering_pc", sa.Integer, nullable=False),
        sa.Column("current_z_score", sa.Float, nullable=False),
        sa.Column("current_label", sa.String(15), nullable=False),
        sa.Column("entry_z_score", sa.Float, nullable=False),
        sa.Column("entry_label", sa.String(15), nullable=False),
        sa.Column("weakening_ratio", sa.Float),
        sa.Column("sign_flipped", sa.Boolean, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.UniqueConstraint("position_id", "timestamp", name="uq_signal_track_position_ts"),
        sa.CheckConstraint("status IN ('HOLD','TRIM','EXIT')", name="ck_signal_track_status"),
    )
    op.create_index(
        "ix_signal_track_position_ts", "position_signal_tracking", ["position_id", "timestamp"]
    )

    # ────────────────────────────────────────────────────────────────
    # 3. hedge_orders
    op.create_table(
        "hedge_orders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("trade_positions.id"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("delta_imbalance_at_trigger", sa.Float, nullable=False),
        sa.Column("rebalance_threshold_used", sa.Float, nullable=False),
        sa.Column("hedge_qty", sa.Integer, nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("ib_order_id", sa.String(40)),
        sa.Column("fill_price", sa.Float),
        sa.Column("commission_usd", sa.Float),
        sa.Column("spread_paid_usd", sa.Float),
        sa.Column("total_cost_usd", sa.Float),
        sa.Column("state", sa.String(15), nullable=False),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_hedge_orders_side"),
        sa.CheckConstraint(
            "state IN ('pending','submitted','filled','failed')",
            name="ck_hedge_orders_state",
        ),
    )
    op.create_index("ix_hedge_position_ts", "hedge_orders", ["position_id", "triggered_at"])

    # ────────────────────────────────────────────────────────────────
    # 4. exit_alerts
    op.create_table(
        "exit_alerts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("trade_positions.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rule_triggered", sa.String(40), nullable=False),
        sa.Column("action_recommended", sa.String(15), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("rule_detail", JSONB_PORTABLE, nullable=False),
        sa.Column("auto_executed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("execution_status", sa.String(20)),
        sa.Column("closing_structure_id", sa.BigInteger, sa.ForeignKey("trade_structures.id")),
        sa.Column("notes", sa.String(500)),
        sa.CheckConstraint(
            "action_recommended IN ('EXIT','TRIM','ALERT_ONLY')",
            name="ck_exit_alerts_action",
        ),
        sa.CheckConstraint(
            "execution_status IS NULL OR execution_status IN ('in_progress','done','failed','overridden')",
            name="ck_exit_alerts_exec_status",
        ),
    )
    op.create_index("ix_exit_alerts_position_ts", "exit_alerts", ["position_id", "timestamp"])

    # ────────────────────────────────────────────────────────────────
    # 5. exit_rules_config (with seed)
    op.create_table(
        "exit_rules_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rule_name", sa.String(40), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("params", JSONB_PORTABLE, nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(40)),
        sa.CheckConstraint("priority BETWEEN 1 AND 10", name="ck_exit_rules_priority"),
    )
    seed_rules = [
        ("signal_reverse", 4,
         {"flip_triggers_exit": True, "weak_threshold": 0.5, "weakening_50pct_triggers_trim": True},
         "Exit if signal flipped or weakened to <0.5 ; trim if weakened >50%"),
        ("time_based", 2,
         {"time_remaining_ratio_threshold": 0.3},
         "Exit if days_remaining / days_at_entry < 0.3"),
        ("stop_loss_vega", 3,
         {"loss_in_vega_units": 3.0},
         "Exit if P&L < -3 × entry_vega"),
        ("time_to_expiry_critical", 5,
         {"min_days_remaining": 7},
         "Hard exit if days_remaining < 7"),
        ("pre_event_regime", 6,
         {"trigger_regimes": ["pre_event"]},
         "Exit any open position if regime becomes pre_event"),
    ]
    for name, priority, params, desc in seed_rules:
        op.execute(
            sa.text(
                "INSERT INTO exit_rules_config (rule_name, priority, params, description) "
                "VALUES (:n, :p, :params, :d)"
            ).bindparams(n=name, p=priority, params=json.dumps(params), d=desc)
        )

    # ────────────────────────────────────────────────────────────────
    # 6. delta_hedge_config (with seed)
    op.create_table(
        "delta_hedge_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("config_name", sa.String(40), nullable=False, unique=True),
        sa.Column("config_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    seed_hedge = [
        ("rebalance_threshold_delta", 0.05, "fraction", "Trigger hedge if |delta| > threshold"),
        ("min_hedge_qty", 1.0, "count", "Skip hedges below this qty (round to 0)"),
        ("max_hedge_frequency_seconds", 300.0, "seconds", "No hedge more often than every 5 min"),
        ("hedge_during_close", 0.0, "boolean", "Continue hedging during position close phase ?"),
    ]
    for name, val, unit, desc in seed_hedge:
        op.execute(
            sa.text(
                "INSERT INTO delta_hedge_config (config_name, config_value, unit, description) "
                "VALUES (:n, :v, :u, :d)"
            ).bindparams(n=name, v=val, u=unit, d=desc)
        )


def downgrade() -> None:
    op.drop_table("delta_hedge_config")
    op.drop_table("exit_rules_config")
    op.drop_index("ix_exit_alerts_position_ts", table_name="exit_alerts")
    op.drop_table("exit_alerts")
    op.drop_index("ix_hedge_position_ts", table_name="hedge_orders")
    op.drop_table("hedge_orders")
    op.drop_index("ix_signal_track_position_ts", table_name="position_signal_tracking")
    op.drop_table("position_signal_tracking")
    op.drop_index("ix_mtm_position_ts", table_name="position_mtm_history")
    op.drop_table("position_mtm_history")
