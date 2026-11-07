"""Natural-key uniques the models now declare (#325, audit 05#5 / 05#6).

* ``open_position.structure`` (the IB localSymbol) is the mirror's key: the
  position sync upserts by it, so two rows for one contract are a bug. The
  unique index was created on the column's old name in migration 028
  (``ix_positions_local_symbol_unique``) and followed the renames; this
  migration (re)creates it under the same name ``IF NOT EXISTS`` so a host
  where it was lost converges and the model's declaration matches.
* one PENDING hedge per position: the delta-hedge loop must never queue two
  hedges for the same leg (a partial unique index on ``state = 'pending'``).

Additive. From this revision the CI runs ``alembic check`` after
``upgrade head``: models and migrations must agree, the drift gate fails
otherwise (``docs/ops/deployment.md``).

Revision ID: 060_schema_uniques
Revises: 059_writer_delivery
Create Date: 2026-11-07
"""
from __future__ import annotations

from alembic import op

revision: str = "060_schema_uniques"
down_revision: str | None = "059_writer_delivery"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_positions_local_symbol_unique "
        "ON open_position (structure)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_hedge_order_pending_per_position "
        "ON hedge_order (position_id) WHERE state = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_hedge_order_pending_per_position")
    # The structure unique predates this revision on every host (028); keep it.
