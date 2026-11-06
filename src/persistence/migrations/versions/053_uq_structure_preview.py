"""UNIQUE ``trade_structure.preview_id`` — one structure per preview (#319).

``/trade/submit`` deduplicated a double submit with a fail-open Redis lock and
a read-then-write of ``trade_preview.user_action`` (audit 04#2 / EX-12). Both
are now fail-closed and the preview row is locked, but the database is the
last line: a second structure for the same preview must be impossible to
persist. NULL stays allowed (closing, unwind and hedge structures have no
preview), and UNIQUE treats NULLs as distinct.

If this migration fails with a duplicate-key error, a double submit already
happened on this database. Find it and decide which structure is the real
one before re-running::

    SELECT preview_id, array_agg(id ORDER BY id) AS structure_ids
    FROM trade_structure WHERE preview_id IS NOT NULL
    GROUP BY preview_id HAVING count(*) > 1;

then ``UPDATE trade_structure SET preview_id = NULL WHERE id = <duplicate>``
(the structure keeps its orders and events; it only loses the preview link).

Revision ID: 053_uq_structure_preview
Revises: 052_spot_order
Create Date: 2026-11-06
"""
from __future__ import annotations

from alembic import op

revision: str = "053_uq_structure_preview"
down_revision: str | None = "052_spot_order"
branch_labels: str | None = None
depends_on: str | None = None

_NAME = "uq_trade_structure_preview_id"


def upgrade() -> None:
    op.create_unique_constraint(_NAME, "trade_structure", ["preview_id"])


def downgrade() -> None:
    op.drop_constraint(_NAME, "trade_structure", type_="unique")
