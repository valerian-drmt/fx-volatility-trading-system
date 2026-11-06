"""Detach ``open_position_history`` from the ``open_position`` FK (#330, audit DI-03).

Closing a position deletes its ``open_position`` row (the IB mirror), and the
``ON DELETE CASCADE`` on ``open_position_history.position_id`` deleted the
whole greeks / P&L history of that leg with it: the primary audit trail of a
trade vanished on close, during normal operation. The October fix
(``passive_deletes``) stopped the OOM, not the loss.

The history keeps ``position_id`` as a plain column (the composite index
``(position_id, timestamp)`` from migration 044 stays, the attribution and
the charts still probe by it); rows simply outlive the mirror row. Retention
is the ``HISTORY_RETENTION_DAYS`` prune, per table, counted
(``persistence.retention``).

The constraint is dropped by lookup, not by name: it was created as
``position_snapshots_position_id_fkey`` (migration 028) and survived two
table renames, but a host where it was recreated by hand may carry another
name. Downgrade deletes the orphans the FK would reject, then restores it.

Revision ID: 058_history_fk_detach
Revises: 057_portfolio_rollups
Create Date: 2026-11-06
"""
from __future__ import annotations

from alembic import op

revision: str = "058_history_fk_detach"
down_revision: str | None = "057_portfolio_rollups"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            fk record;
        BEGIN
            FOR fk IN
                SELECT con.conname
                  FROM pg_constraint con
                  JOIN pg_class rel ON rel.oid = con.conrelid
                  JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
                 WHERE rel.relname = 'open_position_history'
                   AND con.contype = 'f'
                   AND att.attname = 'position_id'
            LOOP
                EXECUTE format('ALTER TABLE open_position_history DROP CONSTRAINT %I', fk.conname);
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    # The FK cannot be restored over rows whose position is gone: remove them
    # first (this is the loss the upgrade prevents; a downgrade accepts it).
    op.execute(
        "DELETE FROM open_position_history h "
        "WHERE NOT EXISTS (SELECT 1 FROM open_position p WHERE p.id = h.position_id)"
    )
    op.create_foreign_key(
        "position_snapshots_position_id_fkey",
        "open_position_history", "open_position",
        ["position_id"], ["id"],
        ondelete="CASCADE",
    )
