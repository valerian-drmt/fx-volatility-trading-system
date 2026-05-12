"""Theme 2 — Portfolio schema refactor.

Variante A+ aggressive (cf. docs/db-schema-theme2-plan.md): 7 tables → 6.
  - 6 RENAMEs for naming convention alignment:
      positions             → position
      position_snapshots    → position_metric_history
      trade_positions       → booked_position
      position_mtm_history  → booked_position_metric_history
      account_snaps         → account_history
      book_state_snapshots  → book_state_snapshot
  - 1 FOLD: position_signal_tracking → booked_position_metric_history
    (adds 7 signal cols, backfills from JOIN, drops the source table).

All renames preserve FKs (Postgres tracks by OID, not name).

Revision ID: 026_theme2_portfolio
Revises: 025_theme3_trade_order
Create Date: 2026-05-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "026_theme2_portfolio"
down_revision: str | None = "025_theme3_trade_order"
branch_labels: str | None = None
depends_on: str | None = None


# Renames done AFTER the signal fold so the fold targets the old name.
_RENAMES: list[tuple[str, str]] = [
    ("position_mtm_history", "booked_position_metric_history"),
    ("positions", "position"),
    ("position_snapshots", "position_metric_history"),
    ("trade_positions", "booked_position"),
    ("account_snaps", "account_history"),
    ("book_state_snapshots", "book_state_snapshot"),
]


def upgrade() -> None:
    # 1. Extend position_mtm_history with 7 signal cols (nullable — most rows
    #    won't have a triggering_pc since not all positions are signal-driven).
    op.add_column("position_mtm_history", sa.Column("triggering_pc", sa.Integer))
    op.add_column("position_mtm_history", sa.Column("current_z_score", sa.Float))
    op.add_column("position_mtm_history", sa.Column("current_label", sa.String(15)))
    op.add_column("position_mtm_history", sa.Column("entry_z_score", sa.Float))
    op.add_column("position_mtm_history", sa.Column("entry_label", sa.String(15)))
    op.add_column("position_mtm_history", sa.Column("weakening_ratio", sa.Float))
    op.add_column("position_mtm_history", sa.Column("sign_flipped", sa.Boolean))
    op.add_column("position_mtm_history", sa.Column("signal_status", sa.String(10)))

    # 2. Backfill the 7 signal cols from position_signal_tracking via JOIN
    #    ON (position_id, timestamp). Rows in position_mtm_history without a
    #    matching signal_tracking row keep NULL — that's the expected
    #    "non-signal-driven position" state.
    op.execute("""
        UPDATE position_mtm_history mtm SET
            triggering_pc    = pst.triggering_pc,
            current_z_score  = pst.current_z_score,
            current_label    = pst.current_label,
            entry_z_score    = pst.entry_z_score,
            entry_label      = pst.entry_label,
            weakening_ratio  = pst.weakening_ratio,
            sign_flipped     = pst.sign_flipped,
            signal_status    = pst.status
          FROM position_signal_tracking pst
         WHERE pst.position_id = mtm.position_id
           AND pst.timestamp   = mtm.timestamp
    """)

    # 3. Drop position_signal_tracking (data is now in mtm).
    op.drop_table("position_signal_tracking")

    # 4. Apply the 6 renames. Postgres rewires FKs by OID automatically.
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    # 1. Reverse the 6 renames (reverse order to be safe).
    for old, new in reversed(_RENAMES):
        op.rename_table(new, old)

    # 2. Recreate position_signal_tracking (schema from migration 016).
    op.create_table(
        "position_signal_tracking",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "position_id", sa.BigInteger,
            sa.ForeignKey("trade_positions.id"), nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triggering_pc", sa.Integer, nullable=False),
        sa.Column("current_z_score", sa.Float, nullable=False),
        sa.Column("current_label", sa.String(15), nullable=False),
        sa.Column("entry_z_score", sa.Float, nullable=False),
        sa.Column("entry_label", sa.String(15), nullable=False),
        sa.Column("weakening_ratio", sa.Float),
        sa.Column("sign_flipped", sa.Boolean, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.UniqueConstraint(
            "position_id", "timestamp", name="uq_signal_track_position_ts",
        ),
        sa.CheckConstraint(
            "status IN ('HOLD','TRIM','EXIT')", name="ck_signal_track_status",
        ),
    )

    # 3. Copy signal cols back where present.
    op.execute("""
        INSERT INTO position_signal_tracking
            (position_id, timestamp, triggering_pc, current_z_score,
             current_label, entry_z_score, entry_label, weakening_ratio,
             sign_flipped, status)
        SELECT
            position_id, timestamp, triggering_pc, current_z_score,
            current_label, entry_z_score, entry_label, weakening_ratio,
            COALESCE(sign_flipped, FALSE), COALESCE(signal_status, 'HOLD')
          FROM position_mtm_history
         WHERE triggering_pc IS NOT NULL
    """)

    # 4. Drop the 8 signal cols from position_mtm_history.
    op.drop_column("position_mtm_history", "signal_status")
    op.drop_column("position_mtm_history", "sign_flipped")
    op.drop_column("position_mtm_history", "weakening_ratio")
    op.drop_column("position_mtm_history", "entry_label")
    op.drop_column("position_mtm_history", "entry_z_score")
    op.drop_column("position_mtm_history", "current_label")
    op.drop_column("position_mtm_history", "current_z_score")
    op.drop_column("position_mtm_history", "triggering_pc")
