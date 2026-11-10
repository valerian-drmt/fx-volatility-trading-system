"""config_vol_engine is append-only, enforced by the database (#329, audit 05#12).

The api only ever INSERTs a new version; a revert is a new row too. Nothing
guaranteed that at the database level: a stray UPDATE or DELETE (a console
session, a future bug) would have rewritten history that backtests and the
audit pane rely on. A BEFORE trigger now refuses both, whatever the role.

Revision ID: 062_config_append_only
Revises: 061_money_numeric
Create Date: 2026-11-10
"""
from __future__ import annotations

from alembic import op

revision: str = "062_config_append_only"
down_revision: str | None = "061_money_numeric"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fxvol_refuse_config_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'config_vol_engine is append-only: % refused (insert a new version instead)', TG_OP
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER config_vol_engine_append_only
            BEFORE UPDATE OR DELETE ON config_vol_engine
            FOR EACH ROW EXECUTE FUNCTION fxvol_refuse_config_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS config_vol_engine_append_only ON config_vol_engine")
    op.execute("DROP FUNCTION IF EXISTS fxvol_refuse_config_mutation()")
