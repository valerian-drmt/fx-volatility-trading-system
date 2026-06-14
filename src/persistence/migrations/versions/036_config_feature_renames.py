"""Tail renames — align the last 3 config/feature tables on the target naming.

Pure ALTER TABLE … RENAME (round-trip unbounded). Completes the R10.1 schema
reconciliation for tables whose r10 final name was reached via a tail migration :

  feature_history_30d  -> feature_history
  vol_engine_config    -> config_vol_engine
  exit_rules_config    -> config_exit_rules

Revision ID: 036_config_feature_renames
Revises: 035_portfolio_renames
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op

revision: str = "036_config_feature_renames"
down_revision: str | None = "035_portfolio_renames"
branch_labels: str | None = None
depends_on: str | None = None


_RENAMES: tuple[tuple[str, str], ...] = (
    ("feature_history_30d", "feature_history"),
    ("vol_engine_config",   "config_vol_engine"),
    ("exit_rules_config",   "config_exit_rules"),
)


def upgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    for old, new in reversed(_RENAMES):
        op.rename_table(new, old)
