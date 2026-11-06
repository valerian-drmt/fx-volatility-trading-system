"""db-writer delivery semantics (#324, audit DI-02 / DI-04 / DI-07).

* ``regime_snapshot_history`` gets the natural key ``(symbol, timestamp)`` the
  other streamed tables already have, so a Redis redelivery or a writer retry
  after an in-doubt commit is a no-op (``ON CONFLICT DO NOTHING``) instead of
  a duplicate row. Existing duplicates (same symbol + timestamp) are collapsed
  onto the lowest id first.
* ``db_writer_dead_letter``: a row the writer could not insert on its own
  (constraint / type error after the per-row fallback) is kept here with the
  error text instead of being dropped with its whole batch. Replay is manual
  (``docs/ops/redis-durability.md``).

Revision ID: 059_writer_delivery
Revises: 058_history_fk_detach
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "059_writer_delivery"
down_revision: str | None = "058_history_fk_detach"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM regime_snapshot_history r
         USING regime_snapshot_history keep
         WHERE r.symbol = keep.symbol
           AND r.timestamp = keep.timestamp
           AND r.id > keep.id
        """
    )
    op.create_unique_constraint(
        "uq_regime_snapshot_symbol_ts", "regime_snapshot_history", ["symbol", "timestamp"],
    )
    op.create_table(
        "db_writer_dead_letter",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("table_name", sa.String(64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_db_writer_dead_letter_created", "db_writer_dead_letter", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_db_writer_dead_letter_created", table_name="db_writer_dead_letter")
    op.drop_table("db_writer_dead_letter")
    op.drop_constraint("uq_regime_snapshot_symbol_ts", "regime_snapshot_history", type_="unique")
