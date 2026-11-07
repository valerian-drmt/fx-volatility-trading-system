"""``portfolio_daily`` + ``pnl_attribution_latest`` (#368, hot/cold T4, closes #286).

The daily-rollup portfolio endpoints (``/stats``, ``/daily-pnl``, ``/header``,
``/var`` fallback, ``/greek-limits``) each ran a ``DISTINCT ON (date_trunc(
'day', timestamp))`` over the whole ``account_history`` table (775k rows,
20 s statement timeout, prod 2026-11-04 / 2026-11-05) on every poll, and
``/pnl-attribution`` re-ran its two LATERAL probes per call. Both results
change slowly: the analytics engine now materialises them every 5 minutes
and the endpoints read one row.

* ``portfolio_daily``: one row per UTC day with the net-liq open / close,
  the day-over-day MTM, the realised P&L of genuine closes, sample counts.
* ``pnl_attribution_latest``: one row per lookback window holding the
  per-position Taylor decomposition (JSONB) + the meta the endpoint echoes.

Additive. Both tables are rebuilt by the engine from the history tables, so
the downgrade just drops them.

Revision ID: 057_portfolio_rollups
Revises: 056_trade_fill_currency
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "057_portfolio_rollups"
down_revision: str | None = "056_trade_fill_currency"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "portfolio_daily",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("net_liq_open", sa.Numeric(15, 2), nullable=True),
        sa.Column("net_liq_close", sa.Numeric(15, 2), nullable=True),
        sa.Column("mtm_usd", sa.Numeric(15, 2), nullable=True),
        sa.Column("realized_usd", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("n_closed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_sample_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sample_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("day", name="uq_portfolio_daily_day"),
    )
    op.create_table(
        "pnl_attribution_latest",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("lookback_hours", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forward_source", sa.String(20), nullable=False),
        sa.Column("n_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "per_position",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.UniqueConstraint("lookback_hours", name="uq_pnl_attribution_latest_lookback"),
    )


def downgrade() -> None:
    op.drop_table("pnl_attribution_latest")
    op.drop_table("portfolio_daily")
