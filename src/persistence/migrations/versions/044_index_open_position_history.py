"""Index ``open_position_history`` (readers + retention).

This table is append-only and grows fast (one row per open position per
risk-engine cycle). It had **no index** beyond the PK, so every reader did a
full table scan once the table got large:

  * ``/portfolio/marginal-var`` and ``/portfolio/pnl-attribution`` filter/group
    by ``(position_id, timestamp)`` → covered by the composite index.
  * the retention prune (``DELETE WHERE timestamp < cutoff``, added with the
    risk-engine daily prune) → covered by the timestamp index.

Index-only, additive, no column change → no writer/reader/payload cascade.

Revision ID: 044_index_open_position_history
Revises: 043_drop_recommended_structure
Create Date: 2026-06-27
"""
from __future__ import annotations

from alembic import op

revision: str = "044_index_open_position_history"
down_revision: str | None = "043_drop_recommended_structure"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "idx_open_position_history_position_ts",
        "open_position_history",
        ["position_id", "timestamp"],
    )
    op.create_index(
        "idx_open_position_history_ts",
        "open_position_history",
        ["timestamp"],
    )


def downgrade() -> None:
    op.drop_index("idx_open_position_history_ts", table_name="open_position_history")
    op.drop_index(
        "idx_open_position_history_position_ts", table_name="open_position_history"
    )
