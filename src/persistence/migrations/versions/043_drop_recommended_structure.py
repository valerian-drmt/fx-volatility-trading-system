"""Drop ``pca_signal_history.recommended_structure``.

Trade suggestions are removed from the product. The desk is decision-support :
it surfaces the relative signal (z-score, label, percentile, loadings,
stability) but does NOT propose what structure to trade — the user picks. So
the PCA signal logic no longer emits ``recommended_structure`` and the column
has no remaining writer or reader.

(The signal→structure catalog ``core.pca_recommendations`` and the trade-preview
"signal mode" that consumed this column were removed in the same change.)

Append-only table, column was nullable and not indexed → a plain drop. The
downgrade re-adds the column (nullable, empty) for rollback symmetry.

Revision ID: 043_drop_recommended_structure
Revises: 042_add_table_comments
Create Date: 2026-06-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "043_drop_recommended_structure"
down_revision: str | None = "042_add_table_comments"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("pca_signal_history", "recommended_structure")


def downgrade() -> None:
    op.add_column(
        "pca_signal_history",
        sa.Column("recommended_structure", sa.String(length=80), nullable=True),
    )
