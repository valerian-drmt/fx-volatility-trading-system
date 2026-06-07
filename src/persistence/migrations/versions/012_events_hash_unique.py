"""events table : add event_hash column + UNIQUE constraint.

Cf. docs/vol_trading_pca/events_pipeline_spec.md §3 — identity = SHA256 of
(event_type|region|scheduled_at_truncated_to_minute), 16 hex chars. Permits
``ON CONFLICT (event_hash) DO NOTHING`` for idempotent upsert across cycles
and across sources.

Backfill : compute hash on existing rows so the UNIQUE constraint can be
enabled without dropping data. SQLite-fallback path is kept for the unit
test fixtures (no PostgreSQL there).

Revision ID: 012_events_hash_unique
Revises: 011_step2_pca_tables
Create Date: 2026-04-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "012_events_hash_unique"
down_revision: str | None = "011_step2_pca_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Add nullable column.
    op.add_column("events", sa.Column("event_hash", sa.String(16), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # On sqlite fallback, leave nullable — Python-side hash on next run.
        return

    # 2. Enable pgcrypto for digest() (idempotent, ships with Postgres).
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # 3. Backfill existing rows via Postgres' digest().
    op.execute("""
        UPDATE events
        SET event_hash = SUBSTRING(
            ENCODE(
                DIGEST(
                    event_type || '|' || region || '|' ||
                    TO_CHAR(
                        DATE_TRUNC('minute', scheduled_at AT TIME ZONE 'UTC'),
                        'YYYY-MM-DD"T"HH24:MI:SS+00:00'
                    ),
                    'sha256'
                ),
                'hex'
            ), 1, 16
        )
        WHERE event_hash IS NULL
    """)

    # 4. Drop rows with duplicate hash before adding UNIQUE constraint
    #    (manual seed + ForexFactory may have inserted the same event twice).
    op.execute("""
        DELETE FROM events
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY event_hash ORDER BY id
                ) AS rn
                FROM events
            ) sub WHERE rn > 1
        )
    """)

    # 5. Lock down.
    op.alter_column("events", "event_hash", nullable=False)
    op.create_unique_constraint("uq_events_event_hash", "events", ["event_hash"])
    op.create_index("ix_events_event_hash", "events", ["event_hash"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_events_event_hash", table_name="events")
        op.drop_constraint("uq_events_event_hash", "events", type_="unique")
    op.drop_column("events", "event_hash")
