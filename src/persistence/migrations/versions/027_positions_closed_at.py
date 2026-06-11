"""Replace ``status`` enum + ``created_at`` audit on positions by ``closed_at``.

After 026 the table mirrors panel E live values. To match the user's
explicit column list (panel E + entry_timestamp + updated_at, nothing else),
we drop :
  - ``created_at`` : redundant with ``entry_timestamp`` for an IB-synced row.
  - ``status``     : an OPEN/CLOSED/EXPIRED string was redundant with the
                     existence of a ``closed_at`` timestamp.

``closed_at`` (nullable) becomes the canonical lifecycle marker :
  - ``IS NULL``  → position is currently open ;
  - ``IS NOT NULL`` → position closed at that timestamp.

Final schema = exactly the columns the user asked for :
  id · local_symbol · side · quantity · maturity · nominal_eur ·
  contract_price_entry · market_price · current_pnl_usd · delta_usd ·
  gamma_usd · vega_usd · theta_usd · entry_timestamp · updated_at · closed_at

(Tenor is computed at API time from ``maturity`` — not persisted, since it
shifts every day as DTE drops.)

Revision ID: 027_positions_closed_at
Revises: 026_positions_live_cols
Create Date: 2026-05-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "027_positions_closed_at"
down_revision: str | None = "026_positions_live_cols"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Add closed_at, backfill from current ``status``.
    op.add_column("positions", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE positions SET closed_at = NOW() WHERE status <> 'OPEN'")

    # 2. Drop dependent indexes / constraints first, then the columns.
    #    NB : ``idx_positions_status_active`` is owned by the 254fc54bb36f
    #    autogen artifact (created in 002, dropped in 254) — it does not exist
    #    at this point in the forward chain, so we neither drop nor recreate it
    #    here (254's downgrade restores it). Touching it would collide with
    #    254's downgrade on a full ``downgrade base`` round-trip.
    op.drop_index("ix_positions_local_symbol_open", table_name="positions")
    op.drop_constraint("ck_positions_status", "positions", type_="check")
    op.drop_column("positions", "status")
    op.drop_column("positions", "created_at")

    # 3. Recreate the partial uniqueness index keyed on the new lifecycle marker.
    op.create_index(
        "ix_positions_local_symbol_open",
        "positions",
        ["local_symbol"],
        postgresql_where=sa.text("closed_at IS NULL"),
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_positions_local_symbol_open", table_name="positions")
    op.add_column(
        "positions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "positions",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        ),
    )
    op.execute("UPDATE positions SET status = 'CLOSED' WHERE closed_at IS NOT NULL")
    op.create_check_constraint(
        "ck_positions_status",
        "positions",
        "status IN ('OPEN','CLOSED','EXPIRED')",
    )
    # ``idx_positions_status_active`` is recreated by 254fc54bb36f's downgrade
    # (it owns that index) — recreating it here would duplicate it.
    op.create_index(
        "ix_positions_local_symbol_open",
        "positions",
        ["local_symbol"],
        postgresql_where=sa.text("status = 'OPEN'"),
        unique=True,
    )
    op.drop_column("positions", "closed_at")
