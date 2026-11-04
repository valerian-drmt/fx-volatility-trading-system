"""Index ``account_history.timestamp`` + drop duplicate ``open_position_history`` indexes.

Prod incident 2026-11-04 : every Postgres-backed endpoint timed out.

* ``account_history`` (775k rows) had **no index beyond the PK**. Every reader
  (``/portfolio/account``, ``/portfolio/header``, equity curve, VaR, …) asks
  for ``ORDER BY timestamp DESC LIMIT 1`` or a time window → full sort per
  call (tens of seconds under load). → ``ix_account_history_ts``.

  The index may already exist on a host where it was created by hand with
  ``CREATE INDEX CONCURRENTLY`` during the incident. ``IF NOT EXISTS`` keeps
  that one ; an INVALID leftover (interrupted concurrent build) is dropped
  first so the migration always ends with a usable index.

* ``open_position_history`` carried two identical pairs of indexes :
  ``ix_position_snapshots_{position_ts,ts}`` (migration 030, table was then
  named ``position_snapshots``) and ``idx_open_position_history_{position_ts,ts}``
  (migration 044, which did not notice the renamed originals). Same columns,
  same order → double write amplification on the hottest table (one row per
  open position per 2s risk cycle). Keep the 044 pair, drop the 030 pair.

Index-only, additive/dedup, no column change → no writer/reader/payload cascade.

Revision ID: 051_acct_ts_idx_dedup
Revises: 050_submit_claims
Create Date: 2026-11-04
"""
from __future__ import annotations

from alembic import op

revision: str = "051_acct_ts_idx_dedup"
down_revision: str | None = "050_submit_claims"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Drop an INVALID leftover (interrupted CREATE INDEX CONCURRENTLY) so that
    # IF NOT EXISTS below does not keep an unusable index.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM pg_index ix
                  JOIN pg_class c ON c.oid = ix.indexrelid
                 WHERE c.relname = 'ix_account_history_ts'
                   AND NOT ix.indisvalid
            ) THEN
                EXECUTE 'DROP INDEX ix_account_history_ts';
            END IF;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_history_ts "
        "ON account_history (timestamp)"
    )
    op.execute("DROP INDEX IF EXISTS ix_position_snapshots_position_ts")
    op.execute("DROP INDEX IF EXISTS ix_position_snapshots_ts")


def downgrade() -> None:
    op.create_index(
        "ix_position_snapshots_ts",
        "open_position_history",
        ["timestamp"],
    )
    op.create_index(
        "ix_position_snapshots_position_ts",
        "open_position_history",
        ["position_id", "timestamp"],
    )
    op.execute("DROP INDEX IF EXISTS ix_account_history_ts")
