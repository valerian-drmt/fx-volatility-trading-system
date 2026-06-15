"""Fold ``position_signal_tracking`` into ``booked_position_metric_history``.

R10.2 — completes the R10.1-deferred Theme-2 fold (the source table is written
by position_monitor and read by an endpoint, so it was held to the feature
wave). Adds 8 nullable signal-trail columns to the metric-history table,
backfills them from position_signal_tracking via JOIN on (position_id,
timestamp), then drops the source table. ``status`` → ``signal_status``.

Revision ID: 039_fold_signal_tracking
Revises: 038_fold_trade_event
Create Date: 2026-06-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "039_fold_signal_tracking"
down_revision: str | None = "038_fold_trade_event"
branch_labels: str | None = None
depends_on: str | None = None

_T = "booked_position_metric_history"


def upgrade() -> None:
    op.add_column(_T, sa.Column("triggering_pc", sa.Integer))
    op.add_column(_T, sa.Column("current_z_score", sa.Float))
    op.add_column(_T, sa.Column("current_label", sa.String(15)))
    op.add_column(_T, sa.Column("entry_z_score", sa.Float))
    op.add_column(_T, sa.Column("entry_label", sa.String(15)))
    op.add_column(_T, sa.Column("weakening_ratio", sa.Float))
    op.add_column(_T, sa.Column("sign_flipped", sa.Boolean))
    op.add_column(_T, sa.Column("signal_status", sa.String(10)))

    op.execute(f"""
        UPDATE {_T} mtm SET
            triggering_pc   = pst.triggering_pc,
            current_z_score = pst.current_z_score,
            current_label   = pst.current_label,
            entry_z_score   = pst.entry_z_score,
            entry_label     = pst.entry_label,
            weakening_ratio = pst.weakening_ratio,
            sign_flipped    = pst.sign_flipped,
            signal_status   = pst.status
          FROM position_signal_tracking pst
         WHERE pst.position_id = mtm.position_id
           AND pst.timestamp   = mtm.timestamp
    """)

    op.drop_table("position_signal_tracking")


def downgrade() -> None:
    op.create_table(
        "position_signal_tracking",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("booked_position.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triggering_pc", sa.Integer, nullable=False),
        sa.Column("current_z_score", sa.Float, nullable=False),
        sa.Column("current_label", sa.String(15), nullable=False),
        sa.Column("entry_z_score", sa.Float, nullable=False),
        sa.Column("entry_label", sa.String(15), nullable=False),
        sa.Column("weakening_ratio", sa.Float),
        sa.Column("sign_flipped", sa.Boolean, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.UniqueConstraint("position_id", "timestamp", name="uq_signal_track_position_ts"),
        sa.CheckConstraint("status IN ('HOLD','TRIM','EXIT')", name="ck_signal_track_status"),
    )
    op.execute(f"""
        INSERT INTO position_signal_tracking
            (position_id, timestamp, triggering_pc, current_z_score, current_label,
             entry_z_score, entry_label, weakening_ratio, sign_flipped, status)
        SELECT
            position_id, timestamp, triggering_pc, current_z_score, current_label,
            entry_z_score, entry_label, weakening_ratio,
            COALESCE(sign_flipped, FALSE), COALESCE(signal_status, 'HOLD')
          FROM {_T}
         WHERE triggering_pc IS NOT NULL
    """)
    for col in (
        "signal_status", "sign_flipped", "weakening_ratio", "entry_label",
        "entry_z_score", "current_label", "current_z_score", "triggering_pc",
    ):
        op.drop_column(_T, col)
