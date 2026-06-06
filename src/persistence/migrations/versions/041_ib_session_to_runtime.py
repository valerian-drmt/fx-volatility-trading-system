"""Rename ``config_ib_session`` → ``runtime_ib_session``.

Why : the row is **runtime state** (UPDATE-in-place singleton, refreshed
~once per heartbeat by ``execution-engine/ib_heartbeat.py``), not a
configuration knob. The earlier ``config_*`` prefix alignment was a
naming convenience but obscured the semantic — a senior DBA would
wince at a "config" table whose rows mutate every few seconds and
never persist a history.

The DB Schema dev tab gets a new ``Runtime`` domain (``^runtime_``
regex) so the visual grouping reflects what the row represents. The
two domains are now well-defined :

    Config  : compile-time / ops tunables, infrequent UPDATE (and
              versioned where it matters — see ``config_vol_engine``).
    Runtime : live-state singletons, UPDATE-in-place at heartbeat
              frequency.

Revision ID: 041_ib_session_to_runtime
Revises: 040_rename_config_history_tables
Create Date: 2026-06-06

Note : the file name and revision ID are short (≤ 32 chars) because
``alembic_version.version_num`` is ``VARCHAR(32)`` in the deployed
schema — a longer ID truncates on UPDATE and the migration fails.
Migration 040 happens to be exactly 32 chars and is the last one that
fits the original verbose pattern.
"""
from __future__ import annotations

from alembic import op

revision: str = "041_ib_session_to_runtime"
down_revision: str | None = "040_rename_config_history_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.rename_table("config_ib_session", "runtime_ib_session")


def downgrade() -> None:
    op.rename_table("runtime_ib_session", "config_ib_session")
