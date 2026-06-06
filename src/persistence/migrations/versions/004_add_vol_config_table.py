"""Append-only versioned ``vol_config`` table (R9 admin config plan T1)

Revision ID: 004_add_vol_config_table
Revises: 003_svi_ssvi_params
Create Date: 2026-04-23 16:00:00.000000+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "004_add_vol_config_table"
down_revision: str | None = "003_svi_ssvi_params"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-only versioned config table.

    - ``version`` is the natural primary key. Each PUT inserts a new row
      with ``version = max(version) + 1`` ; we never UPDATE existing rows.
    - ``config`` holds the full VolTradingConfig JSONB ; schema-less so
      new fields don't need further migrations.
    - Index on ``version DESC`` makes "fetch latest" O(log N) regardless
      of history size.
    - Initial row (version=1) seeded with an empty JSONB so the API
      service falls back to Pydantic defaults on first read.
    """
    op.create_table(
        "vol_config",
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column(
            "config",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("comment", sa.String(500)),
    )
    op.create_index(
        "idx_vol_config_version_desc", "vol_config",
        [sa.text("version DESC")],
    )

    op.execute(
        """
        INSERT INTO vol_config (version, config, updated_by, comment)
        VALUES (1, '{}', 'system',
                'initial seed ; service returns Pydantic defaults on empty config')
        """,
    )


def downgrade() -> None:
    op.drop_index("idx_vol_config_version_desc", table_name="vol_config")
    op.drop_table("vol_config")
