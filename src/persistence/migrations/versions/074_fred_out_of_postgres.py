"""FRED release dates leave Postgres (#307, decision D2).

The FRED terms of use do not allow storing FRED content in a database. The
events pipeline wrote the release dates it fetched into ``event_calendar``.
They now live in a Redis key with a 24 hour expiry (``bus/fred_cache.py``)
and are merged with the other calendars when read.

This deletes the FRED rows already stored. Prod held none on 2026-11-14 (no
FRED key is configured there); a development database may. The backups
taken before this migration still contain whatever the table held: they
expire with the bucket's retention.

The downgrade restores nothing: the rows are fetched again by the scheduler.

Revision ID: 074_fred_out_of_postgres
Revises: 073_partial_fail_review
Create Date: 2026-11-14
"""
from __future__ import annotations

from alembic import op

revision: str = "074_fred_out_of_postgres"
down_revision: str | None = "073_partial_fail_review"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DELETE FROM event_calendar WHERE source = 'FRED'")


def downgrade() -> None:
    """Nothing to restore: FRED rows are not database content any more."""
