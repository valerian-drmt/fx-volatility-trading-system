"""Rename position tables + drop ``updated_at`` in favour of ``timestamp``.

Pure rename pass — no data is touched, no column shape changes :
  - table ``position``                 → ``open_position``
  - table ``position_metric_history``  → ``open_position_history``
  - column ``open_position.updated_at`` → ``open_position.timestamp``

The two open-positions tables now share the same shape, the only
difference being multiplicity : ``open_position`` keeps one row per
contract (current state), ``open_position_history`` keeps a row per
risk-engine cycle (time series). Both carry a ``timestamp`` column so
the two views align at the column level.

The Python ORM classes are also renamed for consistency :
  - ``Position``               → ``OpenPosition``
  - ``PositionMetricHistory``  → ``OpenPositionHistory``
``BookedPosition`` / ``BookedPositionMetricHistory`` are NOT touched —
they describe a different concept (the booked trade lifecycle) and
shouldn't be conflated with the IB-live open book.

Revision ID: 033_open_position_rename
Revises: 032_product_label_dual_column
Create Date: 2026-06-04
"""
from __future__ import annotations

from alembic import op

revision: str = "033_open_position_rename"
down_revision: str | None = "032_product_label_dual_column"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.rename_table("position", "open_position")
    op.rename_table("position_metric_history", "open_position_history")
    op.alter_column(
        "open_position", "updated_at", new_column_name="timestamp",
    )


def downgrade() -> None:
    op.alter_column(
        "open_position", "timestamp", new_column_name="updated_at",
    )
    op.rename_table("open_position_history", "position_metric_history")
    op.rename_table("open_position", "position")
