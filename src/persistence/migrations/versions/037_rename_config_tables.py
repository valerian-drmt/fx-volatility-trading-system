"""Rename ``app_config_scalar`` → ``config_scalar`` and
``ib_session_state`` → ``config_ib_session``.

Why : align all tables in the Config domain on the ``config_`` prefix.
Before this migration the Config domain had heterogeneous naming :

    vol_engine_config           ✓ already config_ at the tail
    app_config_scalar           ❌ prefix was "app_"
    ib_session_state            ❌ prefix was "ib_" (semantically runtime
                                   state, but grouped in Config in the
                                   DbSchema dev tab via the regex
                                   ``/^ib_session/``)
    exit_rules_config           ✓ already config_ at the tail

After :

    config_scalar               (renamed)
    config_ib_session           (renamed — runtime state, keeps the
                                 in-place UPDATE pattern ; the rename
                                 is a naming-only alignment, the row
                                 semantics are unchanged)
    vol_engine_config           (unchanged)
    exit_rules_config           (unchanged)

``\\dt config_*`` in psql now lists the 4 config-domain tables in one
shot.

The matching UniqueConstraint on ``config_scalar`` (was
``uq_app_config_scalar_ns_name``) is renamed too so the alembic
autogenerate diff stays clean on the next run.

Revision ID: 037_rename_config_tables
Revises: 036_rename_pca_surface_snapshot
Create Date: 2026-06-06
"""
from __future__ import annotations

from alembic import op

revision: str = "037_rename_config_tables"
down_revision: str | None = "036_rename_pca_surface_snapshot"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.rename_table("app_config_scalar", "config_scalar")
    op.execute(
        "ALTER TABLE config_scalar RENAME CONSTRAINT "
        "uq_app_config_scalar_ns_name TO uq_config_scalar_ns_name"
    )
    op.rename_table("ib_session_state", "config_ib_session")


def downgrade() -> None:
    op.rename_table("config_ib_session", "ib_session_state")
    op.rename_table("config_scalar", "app_config_scalar")
    op.execute(
        "ALTER TABLE app_config_scalar RENAME CONSTRAINT "
        "uq_config_scalar_ns_name TO uq_app_config_scalar_ns_name"
    )
