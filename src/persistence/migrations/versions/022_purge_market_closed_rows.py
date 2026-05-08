"""Purge ``vol_surface_snapshot`` rows where the market was closed.

When IB returns no quote (markets-closed, weekend, holiday) the bid/ask come
back as ``-1`` ; the market-data engine used to publish that as a tick, so
the vol-engine wrote a row to ``vol_surface_snapshot`` with ``spot = -1`` and
all the downstream cycle outputs (regime, PCA projection, hourly snapshot,
feature history) carried garbage too.

The publish path is now hardened (``main.py`` filters ``bid <= 0`` /
``ask <= 0`` ; ``_read_spot`` rejects ``spot <= 0``) so future cycles can't
reproduce the issue. This migration cleans up the rows that already landed.

Cascade order matters : delete the dependents first (they reference the
surface row's timestamp + symbol), then drop the surface rows themselves.

Revision ID: 022_purge_market_closed_rows
Revises: 021_drop_pricing_signal_snapshot
Create Date: 2026-05-07
"""
from __future__ import annotations

from alembic import op

revision: str = "022_purge_market_closed_rows"
down_revision: str | None = "021_drop_pricing_signal_snapshot"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Snapshot the bad (timestamp, symbol) pairs into a temp table so every
    # dependent DELETE references the same set even if vol_surface_snapshot
    # gets new writes during the migration.
    op.execute(
        """
        CREATE TEMP TABLE _bad_cycles AS
        SELECT timestamp, underlying AS symbol
          FROM vol_surface_snapshot
         WHERE spot IS NULL OR spot <= 0
        """
    )

    # regime_feature_snapshot uses (symbol, timestamp).
    op.execute(
        """
        DELETE FROM regime_feature_snapshot rfs
         USING _bad_cycles bc
         WHERE rfs.symbol = bc.symbol AND rfs.timestamp = bc.timestamp
        """
    )

    # feature_history_30d uses (symbol, timestamp).
    op.execute(
        """
        DELETE FROM feature_history_30d fh
         USING _bad_cycles bc
         WHERE fh.symbol = bc.symbol AND fh.timestamp = bc.timestamp
        """
    )

    # pca_projection_snapshot uses (symbol, timestamp).
    op.execute(
        """
        DELETE FROM pca_projection_snapshot pps
         USING _bad_cycles bc
         WHERE pps.symbol = bc.symbol AND pps.timestamp = bc.timestamp
        """
    )

    # surface_snapshots_hourly is a downsample of vol_surface_snapshot —
    # delete by the same key shape.
    op.execute(
        """
        DELETE FROM surface_snapshots_hourly ssh
         USING _bad_cycles bc
         WHERE ssh.symbol = bc.symbol AND ssh.timestamp = bc.timestamp
        """
    )

    # Finally, the bad surface rows themselves.
    op.execute(
        """
        DELETE FROM vol_surface_snapshot
         WHERE spot IS NULL OR spot <= 0
        """
    )


def downgrade() -> None:
    # Irreversible : the deleted rows carried no real data. Re-running the
    # vol-engine over the same wall-clock timestamps would not reproduce them
    # because the publish path now rejects sentinel ticks.
    pass
