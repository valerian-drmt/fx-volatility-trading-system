"""Step 3 — Trade preview tables.

Cf. docs/vol_trading_pca/specs/STEP3_TRADE_PREVIEW.md §5.

Tables :
  - structure_definitions   : catalogue 6 structures (display, leg_template JSONB)
  - trade_previews          : 1 row per "Arm trade" click (audit + state)
  - book_state_snapshots    : current is_active=true row per symbol + history
  - risk_limits             : hot-reloadable risk parameters

pricing_cache is intentionally skipped — the spec marks it optional and we
don't have a perf bottleneck yet.

Revision ID: 013_step3_trade_preview_tables
Revises: 012_events_hash_unique
Create Date: 2026-05-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013_step3_trade_preview_tables"
down_revision: str | None = "012_events_hash_unique"
branch_labels: str | None = None
depends_on: str | None = None

JSONB_PORTABLE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "structure_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("structure_type", sa.String(40), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("leg_template", JSONB_PORTABLE, nullable=False),
        sa.Column("min_legs", sa.Integer, nullable=False),
        sa.Column("max_legs", sa.Integer, nullable=False),
        sa.Column("requires_delta_hedge", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("typical_vega_sign", sa.String(10), nullable=False),
        sa.Column("typical_gamma_sign", sa.String(10), nullable=False),
        sa.Column("typical_theta_sign", sa.String(10), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("rationale_for_pc", sa.String(300)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "typical_vega_sign IN ('positive','negative','neutral')",
            name="ck_struct_def_vega_sign",
        ),
    )

    seeds = [
        ("straddle_atm", "Long straddle ATM",
         [{"contract_type": "call", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
          {"contract_type": "put",  "delta_pillar": "atm", "side": "BUY", "qty_factor": 1}],
         2, 2, True, "positive", "positive", "negative",
         "Buy ATM call + ATM put", "PC1 CHEAP : level low → buy vol"),
        ("short_strangle", "Short OTM strangle",
         [{"contract_type": "call", "delta_pillar": "25dc", "side": "SELL", "qty_factor": 1},
          {"contract_type": "put",  "delta_pillar": "25dp", "side": "SELL", "qty_factor": 1}],
         2, 2, True, "negative", "negative", "positive",
         "Sell 25d strangle", "PC1 EXPENSIVE : level high → sell vol"),
        ("calendar_long", "Calendar buy long-dated",
         [{"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near", "side": "SELL", "qty_factor": 1},
          {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",  "side": "BUY",  "qty_factor": 1}],
         2, 2, True, "positive", "neutral", "neutral",
         "Sell near, buy far", "PC2 CHEAP : term inverted"),
        ("calendar_short", "Calendar sell long-dated",
         [{"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near", "side": "BUY",  "qty_factor": 1},
          {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",  "side": "SELL", "qty_factor": 1}],
         2, 2, True, "negative", "neutral", "neutral",
         "Buy near, sell far", "PC2 EXPENSIVE : term steep"),
        ("long_butterfly_25d", "Long butterfly (10d wings)",
         [{"contract_type": "call", "delta_pillar": "10dc", "side": "BUY",  "qty_factor": 1},
          {"contract_type": "call", "delta_pillar": "atm",  "side": "SELL", "qty_factor": 2},
          {"contract_type": "call", "delta_pillar": "10dp", "side": "BUY",  "qty_factor": 1}],
         3, 3, True, "neutral", "neutral", "neutral",
         "Long wings, short body", "PC3 CHEAP : wings cheap"),
        ("short_butterfly_25d", "Short butterfly (10d wings)",
         [{"contract_type": "call", "delta_pillar": "10dc", "side": "SELL", "qty_factor": 1},
          {"contract_type": "call", "delta_pillar": "atm",  "side": "BUY",  "qty_factor": 2},
          {"contract_type": "call", "delta_pillar": "10dp", "side": "SELL", "qty_factor": 1}],
         3, 3, True, "neutral", "neutral", "neutral",
         "Short wings, long body", "PC3 EXPENSIVE : wings rich"),
    ]
    op.bulk_insert(
        sa.table(
            "structure_definitions",
            sa.column("structure_type", sa.String),
            sa.column("display_name", sa.String),
            sa.column("leg_template", JSONB_PORTABLE),
            sa.column("min_legs", sa.Integer),
            sa.column("max_legs", sa.Integer),
            sa.column("requires_delta_hedge", sa.Boolean),
            sa.column("typical_vega_sign", sa.String),
            sa.column("typical_gamma_sign", sa.String),
            sa.column("typical_theta_sign", sa.String),
            sa.column("description", sa.String),
            sa.column("rationale_for_pc", sa.String),
        ),
        [
            {
                "structure_type": st, "display_name": dn, "leg_template": lt,
                "min_legs": ml, "max_legs": mx, "requires_delta_hedge": rdh,
                "typical_vega_sign": vs, "typical_gamma_sign": gs, "typical_theta_sign": ts,
                "description": desc, "rationale_for_pc": rat,
            }
            for (st, dn, lt, ml, mx, rdh, vs, gs, ts, desc, rat) in seeds
        ],
    )

    op.create_table(
        "trade_previews",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("preview_id", sa.String(40), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pca_signal_id", sa.BigInteger, sa.ForeignKey("pca_signals.id")),
        sa.Column("triggering_pc", sa.Integer),
        sa.Column("armed_z_score", sa.Numeric(10, 4)),
        sa.Column("armed_signal_label", sa.String(15)),
        sa.Column("structure_type", sa.String(40), nullable=False),
        sa.Column("reference_tenor", sa.String(10), nullable=False),
        sa.Column("structure_full_payload", JSONB_PORTABLE, nullable=False),
        sa.Column("state", sa.String(25), nullable=False),
        sa.Column("pre_submit_checks", JSONB_PORTABLE, nullable=False),
        sa.Column("blocking_reasons", JSONB_PORTABLE),
        sa.Column("user_action", sa.String(20)),
        sa.Column("user_action_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_trade_id", sa.BigInteger),
        sa.CheckConstraint(
            "state IN ('valid_for_submit','blocked','expired','submitted','cancelled')",
            name="ck_trade_previews_state",
        ),
    )
    op.create_index("ix_trade_previews_created", "trade_previews", ["created_at"])
    op.create_index("ix_trade_previews_state", "trade_previews", ["state", "created_at"])

    op.create_table(
        "book_state_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("symbol", sa.String(20), nullable=False, server_default="EURUSD"),
        sa.Column("total_vega_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_gamma_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_theta_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_delta", sa.Float, nullable=False, server_default="0"),
        sa.Column("vega_by_tenor", JSONB_PORTABLE),
        sa.Column("vega_by_pc_source", JSONB_PORTABLE),
        sa.Column("n_open_structures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_open_legs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notional_engaged_usd", sa.Float),
        sa.Column("capital_total_usd", sa.Float),
        sa.Column("margin_used_usd", sa.Float),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_book_state_current", "book_state_snapshots",
        ["symbol", "is_current"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "risk_limits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("limit_name", sa.String(60), nullable=False, unique=True),
        sa.Column("limit_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(40)),
    )
    risk_seeds = [
        ("max_loss_per_trade_pct", 2.0, "pct_capital", "Max loss per trade as % of capital"),
        ("max_book_vega_usd", 5000.0, "usd", "Max total book vega"),
        ("max_book_vega_per_tenor_usd", 2000.0, "usd", "Max vega per single tenor"),
        ("max_n_open_structures", 8, "count", "Max simultaneous open structures"),
        ("max_iv_data_age_seconds", 120, "count", "IV data must be < 2min old"),
        ("min_liquidity_quoted_size", 10, "count", "Minimum quoted size on legs"),
        ("preview_validity_seconds", 120, "count", "Trade preview valid for 2min"),
        ("base_qty", 10, "count", "Base position size"),
        ("z_threshold_min", 1.5, "count", "Min |z| considered actionable"),
        ("max_z_multiplier", 2.0, "count", "Cap on z-score sizing factor"),
        ("book_alpha", 0.3, "count", "Book penalty exponent (0..1)"),
        ("book_vega_neutral_threshold", 2000.0, "usd", "Vega above this triggers book_penalty"),
        ("starting_capital_usd", 100000.0, "usd", "Bootstrap capital for first preview"),
    ]
    op.bulk_insert(
        sa.table(
            "risk_limits",
            sa.column("limit_name", sa.String),
            sa.column("limit_value", sa.Float),
            sa.column("unit", sa.String),
            sa.column("description", sa.String),
        ),
        [{"limit_name": ln, "limit_value": lv, "unit": u, "description": d}
         for (ln, lv, u, d) in risk_seeds],
    )


def downgrade() -> None:
    op.drop_table("risk_limits")
    op.drop_index("ix_book_state_current", table_name="book_state_snapshots")
    op.drop_table("book_state_snapshots")
    op.drop_index("ix_trade_previews_state", table_name="trade_previews")
    op.drop_index("ix_trade_previews_created", table_name="trade_previews")
    op.drop_table("trade_previews")
    op.drop_table("structure_definitions")
