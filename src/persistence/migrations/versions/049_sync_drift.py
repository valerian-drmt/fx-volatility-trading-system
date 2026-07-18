"""Sync 3 live-DB drifts surfaced by the DB Schema dev tab (DIFF mode).

Brings the live database back in line with ``models.py``:

1. ``event_calendar.event_hash`` → DROP NOT NULL. The ORM declares it
   nullable (``Mapped[str | None]``) — legacy seed rows predate hashing
   and the pipeline tolerates NULL (dedup skips them).
2. ``trade_event.payload`` → JSON → JSONB. ``JSONB_PORTABLE`` on the ORM;
   PG needs the explicit ``USING`` cast (the autogenerate blind spot
   documented in docs/db_schema_drift_workflow.md).
3. ``runtime_ib_session.n_disconnects_24h`` → backfill NULLs to 0, then
   SET NOT NULL (ORM: ``Mapped[int]`` with default 0).

All three tables are small (events calendar / audit log / one-row runtime
session), so the ACCESS EXCLUSIVE locks are instantaneous.

Revision ID: 049_sync_drift
Revises: 048_reconciliation_break
Create Date: 2026-07-16
"""
from __future__ import annotations

from alembic import op

revision: str = "049_sync_drift"
down_revision: str | None = "048_reconciliation_break"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. ORM says nullable — relax the live NOT NULL.
    op.execute("ALTER TABLE event_calendar ALTER COLUMN event_hash DROP NOT NULL;")
    # 2. JSON → JSONB needs the USING cast (autogenerate emits it without one).
    op.execute("ALTER TABLE trade_event ALTER COLUMN payload TYPE JSONB USING payload::JSONB;")
    # 3. Backfill first so SET NOT NULL can't fail on legacy NULL rows.
    op.execute("UPDATE runtime_ib_session SET n_disconnects_24h = 0 WHERE n_disconnects_24h IS NULL;")
    op.execute("ALTER TABLE runtime_ib_session ALTER COLUMN n_disconnects_24h SET NOT NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE runtime_ib_session ALTER COLUMN n_disconnects_24h DROP NOT NULL;")
    op.execute("ALTER TABLE trade_event ALTER COLUMN payload TYPE JSON USING payload::JSON;")
    # Restore NOT NULL: backfill any NULL hashes with a unique 16-char stand-in
    # derived from the row id (uq_events_event_hash stays satisfied).
    op.execute(
        "UPDATE event_calendar SET event_hash = substr(md5('ec-' || id::text), 1, 16) "
        "WHERE event_hash IS NULL;"
    )
    op.execute("ALTER TABLE event_calendar ALTER COLUMN event_hash SET NOT NULL;")
