"""add indices and GIN on JSONB — R1 PR #6

Revision ID: 002_add_indices
Revises: 001_initial_schema
Create Date: 2026-04-17 17:10:00.000000+00:00

Adds the full set of indexes specified in
releases/architecture_finale_project/08-postgresql.md:

    positions
        idx_positions_symbol_status   (symbol, status)
        idx_positions_entry_ts         (entry_timestamp DESC)
        idx_positions_status_active    (status) partial WHERE status='OPEN'

    position_snapshots
        idx_pos_snaps_position_ts      (position_id, timestamp DESC)
        idx_pos_snaps_ts               (timestamp DESC)

    vol_surfaces
        idx_vol_surf_underlying_ts     (underlying, timestamp DESC)
        idx_vol_surf_ts                (timestamp DESC)
        idx_vol_surf_data_gin          USING GIN (surface_data)

    signals
        idx_signals_underlying_tenor_ts (underlying, tenor, timestamp DESC)
        idx_signals_type_ts             (signal_type, timestamp DESC)
        idx_signals_ts                  (timestamp DESC)

    trades
        idx_trades_position             (position_id)
        idx_trades_ts                   (timestamp DESC)

    account_snaps
        idx_account_ts                  (timestamp DESC)

    backtest_runs
        idx_backtest_strategy           (strategy_name)
        idx_backtest_created            (created_at DESC)

Notes:
    * Autogenerate cannot inspect DESC sort direction or partial predicates,
      so every op.create_index() is hand-written here.
    * The GIN index is PostgreSQL-specific. If the dialect is not postgresql
      (dev SQLite), the GIN index is skipped to keep migrations portable.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "002_add_indices"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    # ----- positions -----
    op.create_index(
        "idx_positions_symbol_status",
        "positions",
        ["symbol", "status"],
    )
    op.create_index(
        "idx_positions_entry_ts",
        "positions",
        [text("entry_timestamp DESC")],
    )
    op.create_index(
        "idx_positions_status_active",
        "positions",
        ["status"],
        postgresql_where=text("status = 'OPEN'"),
    )

    # ----- position_snapshots -----
    op.create_index(
        "idx_pos_snaps_position_ts",
        "position_snapshots",
        ["position_id", text("timestamp DESC")],
    )
    op.create_index(
        "idx_pos_snaps_ts",
        "position_snapshots",
        [text("timestamp DESC")],
    )

    # ----- vol_surfaces -----
    op.create_index(
        "idx_vol_surf_underlying_ts",
        "vol_surfaces",
        ["underlying", text("timestamp DESC")],
    )
    op.create_index(
        "idx_vol_surf_ts",
        "vol_surfaces",
        [text("timestamp DESC")],
    )
    if _is_postgres():
        op.create_index(
            "idx_vol_surf_data_gin",
            "vol_surfaces",
            ["surface_data"],
            postgresql_using="gin",
        )

    # ----- signals -----
    op.create_index(
        "idx_signals_underlying_tenor_ts",
        "signals",
        ["underlying", "tenor", text("timestamp DESC")],
    )
    op.create_index(
        "idx_signals_type_ts",
        "signals",
        ["signal_type", text("timestamp DESC")],
    )
    op.create_index(
        "idx_signals_ts",
        "signals",
        [text("timestamp DESC")],
    )

    # ----- trades -----
    op.create_index(
        "idx_trades_position",
        "trades",
        ["position_id"],
    )
    op.create_index(
        "idx_trades_ts",
        "trades",
        [text("timestamp DESC")],
    )

    # ----- account_snaps -----
    op.create_index(
        "idx_account_ts",
        "account_snaps",
        [text("timestamp DESC")],
    )

    # ----- backtest_runs -----
    op.create_index(
        "idx_backtest_strategy",
        "backtest_runs",
        ["strategy_name"],
    )
    op.create_index(
        "idx_backtest_created",
        "backtest_runs",
        [text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_backtest_created", table_name="backtest_runs")
    op.drop_index("idx_backtest_strategy", table_name="backtest_runs")
    op.drop_index("idx_account_ts", table_name="account_snaps")
    op.drop_index("idx_trades_ts", table_name="trades")
    op.drop_index("idx_trades_position", table_name="trades")
    op.drop_index("idx_signals_ts", table_name="signals")
    op.drop_index("idx_signals_type_ts", table_name="signals")
    op.drop_index("idx_signals_underlying_tenor_ts", table_name="signals")
    if _is_postgres():
        op.drop_index("idx_vol_surf_data_gin", table_name="vol_surfaces")
    op.drop_index("idx_vol_surf_ts", table_name="vol_surfaces")
    op.drop_index("idx_vol_surf_underlying_ts", table_name="vol_surfaces")
    op.drop_index("idx_pos_snaps_ts", table_name="position_snapshots")
    op.drop_index("idx_pos_snaps_position_ts", table_name="position_snapshots")
    op.drop_index("idx_positions_status_active", table_name="positions")
    op.drop_index("idx_positions_entry_ts", table_name="positions")
    op.drop_index("idx_positions_symbol_status", table_name="positions")
