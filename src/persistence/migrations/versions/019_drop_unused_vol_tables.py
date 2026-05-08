"""Drop 4 unused vol-domain tables.

- ``svi_params`` : per-tenor SVI fits. Same data lives in
  ``vol_surfaces.surface_data._svi`` (JSONB). Only reader was a
  cockpit row-count metric.
- ``ssvi_params`` : surface-level SSVI fit. Same data in
  ``vol_surfaces.surface_data._ssvi``. No readers at all.
- ``vol_features_context_baseline`` : E3 weekly batch baseline that
  never shipped. ``regime_features._lookup_baseline`` now computes
  μ/σ live from regime_snapshots history with progressive context
  relaxation, covering the same use cases without an extra table.
- ``backtest_runs`` : no live consumer. The ``/api/v1/backtest`` route
  + analytics_service.list_backtests are dropped alongside.

This is irreversible — downgrade re-creates empty stubs but the
historical rows are gone.

Revision ID: 019_drop_unused_vol_tables
Revises: 018_features_enrichment
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "019_drop_unused_vol_tables"
down_revision: str | None = "018_features_enrichment"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("svi_params")
    op.drop_table("ssvi_params")
    op.drop_table("vol_features_context_baseline")
    op.drop_table("backtest_runs")


def downgrade() -> None:
    """Re-create empty stubs. Historical data is not recovered."""
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
    op.create_table(
        "vol_features_context_baseline",
        sa.Column("feature", sa.String(20), primary_key=True),
        sa.Column("event_type", sa.String(20), primary_key=True),
        sa.Column("days_bucket", sa.Integer(), primary_key=True),
        sa.Column("tod_bucket", sa.String(20), primary_key=True),
        sa.Column("mu", sa.Float(), nullable=False),
        sa.Column("sigma", sa.Float(), nullable=False),
        sa.Column("n_obs", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('valid','insufficient','stale')",
            name="ck_vol_features_context_baseline_status",
        ),
    )
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_name", sa.String(50), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("sharpe_ratio", sa.Numeric(8, 4)),
        sa.Column("sortino_ratio", sa.Numeric(8, 4)),
        sa.Column("max_drawdown_pct", sa.Numeric(8, 4)),
        sa.Column("max_drawdown_duration_days", sa.Integer()),
        sa.Column("hit_rate", sa.Numeric(6, 4)),
        sa.Column("total_return_pct", sa.Numeric(10, 4)),
        sa.Column("annualized_return_pct", sa.Numeric(10, 4)),
        sa.Column("annualized_vol_pct", sa.Numeric(8, 4)),
        sa.Column("n_trades", sa.Integer()),
        sa.Column("avg_holding_period_days", sa.Numeric(8, 2)),
        sa.Column("profit_factor", sa.Numeric(8, 4)),
        sa.Column("equity_curve", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("trades_log", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
