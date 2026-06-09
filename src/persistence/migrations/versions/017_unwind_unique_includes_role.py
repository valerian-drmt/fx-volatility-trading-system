"""Replace UNIQUE(structure_id, leg_idx) with UNIQUE(structure_id, leg_idx, order_role).

The original constraint (cf. migration 014) prevented multiple ``structure_orders``
rows on the same ``leg_idx``. Once rollback (Passe B) is wired, an unwind order
on the same physical leg as an entry needs its own row — same ``leg_idx``,
different ``order_role``. Same applies to closing orders (STEP5 §9.3) and
hedge orders.

Cf. ``docs/vol_trading_pca/specs/STEP4_EXECUTION.md`` §7.3 (rollback partial fills).

Revision ID: 017_unwind_unique_includes_role
Revises: 016_step5_position_monitoring
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op

revision: str = "017_unwind_unique_includes_role"
down_revision: str | None = "016_step5_position_monitoring"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_structure_orders_structure_leg", "structure_orders", type_="unique",
    )
    op.create_unique_constraint(
        "uq_structure_orders_structure_leg_role",
        "structure_orders",
        ["structure_id", "leg_idx", "order_role"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_structure_orders_structure_leg_role", "structure_orders", type_="unique",
    )
    op.create_unique_constraint(
        "uq_structure_orders_structure_leg", "structure_orders",
        ["structure_id", "leg_idx"],
    )
