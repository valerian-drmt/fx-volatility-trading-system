"""Autovacuum tuned for the two append-and-prune history tables (#373, T9).

``open_position_history`` and ``booked_position_metric_history`` take a
steady stream of inserts and one batched delete a night. With the default
scale factor (20 % of the table) autovacuum would let the dead tuples of a
night's prune sit for days on a 90-day table; 2 % keeps the bloat and the
hot-read p95 flat. Storage parameters only: no data moves, and ``alembic
check`` does not compare them.

Revision ID: 063_history_autovacuum
Revises: 062_config_append_only
Create Date: 2026-11-10
"""
from __future__ import annotations

from alembic import op

revision: str = "063_history_autovacuum"
down_revision: str | None = "062_config_append_only"
branch_labels: str | None = None
depends_on: str | None = None

TABLES = ("open_position_history", "booked_position_metric_history")


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} SET ("
            "autovacuum_vacuum_scale_factor = 0.02, "
            "autovacuum_analyze_scale_factor = 0.01, "
            "autovacuum_vacuum_cost_delay = 2)"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} RESET ("
            "autovacuum_vacuum_scale_factor, autovacuum_analyze_scale_factor, autovacuum_vacuum_cost_delay)"
        )
