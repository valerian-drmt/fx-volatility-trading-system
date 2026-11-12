"""Read-only role for analysis queries against prod (``fxvol_reader``).

SELECT on every table in ``public``, today's and tomorrow's (default
privileges of the owner role), and nothing else. Session defaults make it safe
to point at the live database: every transaction is read-only, a statement is
cut at 30 s, an idle transaction at 60 s, and at most 3 connections.

The role is created NOLOGIN. On prod, ``infrastructure/ec2/remote-deploy.sh``
grants LOGIN with the password from ``/fxvol/prod/DB_READONLY_PASSWORD``
(sent as a SCRAM verifier, never in clear) when that parameter exists, and
revokes it when it does not. Access path: docs/ops/runbooks.md section 5.

Roles are cluster-wide, so the upgrade is idempotent and the downgrade drops
the role only after removing its grants.

Revision ID: 064_reader_role
Revises: 063_history_autovacuum
Create Date: 2026-11-12
"""
from __future__ import annotations

from alembic import op

revision: str = "064_reader_role"
down_revision: str | None = "063_history_autovacuum"
branch_labels: str | None = None
depends_on: str | None = None

ROLE = "fxvol_reader"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {ROLE}', current_database());
        END
        $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}")
    op.execute(f"GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE}")
    # Tables created later by migrations (run as the owner role) follow.
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO {ROLE}")
    op.execute(f"ALTER ROLE {ROLE} SET default_transaction_read_only = on")
    op.execute(f"ALTER ROLE {ROLE} SET statement_timeout = '30s'")
    op.execute(f"ALTER ROLE {ROLE} SET idle_in_transaction_session_timeout = '60s'")
    op.execute(f"ALTER ROLE {ROLE} CONNECTION LIMIT 3")


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON SEQUENCES FROM {ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                EXECUTE 'DROP OWNED BY {ROLE}';
                DROP ROLE {ROLE};
            END IF;
        END
        $$
        """
    )
