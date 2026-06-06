"""Add ``COMMENT ON TABLE`` for every business table.

Why : ``\\d table_name`` in psql, a senior DBA's first reflex, shows the
column types but no context on what the table actually models. The
docstrings on the ORM classes carry that context, but only
SQLAlchemy-aware tooling reads them. ``COMMENT ON TABLE`` brings the
same context to anyone connected directly to Postgres (DBeaver, psql,
the in-app DB Schema dev tab).

Comments are kept short — one line per table, "what kind of rows live
here + dominant write pattern (singleton / append-only / mirror)".
Column-level COMMENTs are NOT applied here ; the column names are
already self-describing, and chasing every column would produce a
fragile migration.

The strings here also appear as ``comment="..."`` arguments on the ORM
classes (added in a follow-up code commit) so alembic --autogenerate
stays clean. The migration only touches DB metadata, no row movement.

Revision ID: 042_add_table_comments
Revises: 041_rename_config_ib_session_runtime
Create Date: 2026-06-06
"""
from __future__ import annotations

from alembic import op

revision: str = "042_add_table_comments"
down_revision: str | None = "041_rename_config_ib_session_runtime"
branch_labels: str | None = None
depends_on: str | None = None


# {table_name: comment} — keep alphabetised so an alphabetic ``\\dt``
# in psql lines up with the order here.
TABLE_COMMENTS: dict[str, str] = {
    # ─── Trading domain ───
    "account_history":
        "Per-cycle audit snapshot of the IB account (cash, margin, gross/net liq). "
        "Append-only ; written by execution-engine on each account-summary update.",
    "book_state_snapshot_history":
        "Audit log of every successful Submit — captures the full book state right "
        "after the new trade is open. Append-only.",
    "booked_position":
        "User-level trades booked into the book (1 row per Submit). "
        "Append-only ; lifecycle states tracked via state column.",
    "booked_position_metric_history":
        "Per-cycle greeks + P&L snapshot per booked_position row. "
        "Append-only ; written by risk-engine every cycle.",
    "exit_alert":
        "Triggered exit alerts pending operator review. "
        "Append-only ; consumed by the exit-alert panel.",
    "hedge_order":
        "EUR FUT delta-hedge orders submitted by the delta-hedger loop. "
        "Append-only ; one row per fire.",
    "open_position":
        "Live IB-side positions reconciled against ib_insync. UPDATE-in-place "
        "by position-sync (5 s cadence) ; row deleted when fully closed.",
    "open_position_history":
        "Per-cycle greeks + P&L snapshot per open_position row. Append-only ; "
        "written by risk-engine every cycle.",
    "package":
        "Murex-style operational grouping of multiple trade_structure rows. "
        "Populated by manual API call ; FK target of trade_structure.package_id.",
    "trade_event":
        "Audit log of every order action (submit, cancel, fill, reject) tied "
        "to a trade_structure row. Append-only ; replaces the legacy "
        "execution_audit_log.",
    "trade_fill":
        "Per-leg fill confirmations from IB. Append-only ; one row per fill, "
        "linked to trade_order.id.",
    "trade_order":
        "Active IB orders submitted as part of a trade_structure. "
        "UPDATE-in-place on status transitions ; long-lived for audit.",
    "trade_preview":
        "Audit log : one row per Arm click on the UI. Tracks the (signal, "
        "structure, state) tuple for 90 s — either Submit promotes it to a "
        "trade_structure or it expires.",
    "trade_structure":
        "Root of the Murex package > trade > contract hierarchy. One row per "
        "user-confirmed Submit ; lifecycle tracked via state column.",

    # ─── Volatility / surface analytics ───
    "vol_surface_history":
        "Per-cycle full vol surface fit (SVI/SSVI params + ATM per tenor) "
        "stored as JSONB. Append-only ; written by vol-engine each cycle.",

    # ─── PCA factor model ───
    "pca_model":
        "Versioned PCA fit (means / stds / loadings / eigenvalues as JSONB). "
        "Append-only ; is_active=true marks the live model.",
    "pca_signal_history":
        "One row per (PC, cycle) — z-score, label (CHEAP/FAIR/EXPENSIVE), "
        "actionable flag, recommended structure. Append-only.",
    "pca_surface_snapshot_history":
        "30-dimensional hourly snapshot of the vol surface (6 tenors x 5 "
        "deltas). The PCA fit input. Append-only.",

    # ─── Regime / events ───
    "event_calendar":
        "Catalogue of economic events scraped from FRED / ECB / BoE / FOMC / "
        "Eurostat / ONS. Idempotent UPSERT keyed by event_hash.",
    "feature_history":
        "Wide-format timeseries of derived vol features (ATM IV per tenor, "
        "rv_yz, vol_of_vol, term_slope, z-scores). Append-only.",
    "regime_snapshot_history":
        "One row per vol-engine cycle — regime label, the features that drove "
        "the classification, GMM probas, event_dampener. Append-only.",

    # ─── Config (compile-time + ops tunables) ───
    "config_exit_rules":
        "Versioned set of systematic exit rules (z-flip / time / stop-loss / "
        "DTE thresholds). Append-only ; latest row is the source of truth.",
    "config_scalar":
        "Generic scalar key/value config — namespace='risk' for trade-gating "
        "limits, namespace='delta_hedge' for the hedger loop. UPDATE-in-place.",
    "config_vol_engine":
        "Versioned full vol-engine config (Pydantic JSON blob). Append-only ; "
        "engines hot-reload via Redis pub/sub on insert.",

    # ─── Runtime state ───
    "runtime_ib_session":
        "Singleton row tracking the IB session state — heartbeat, account "
        "type (paper/live), cash + margin. UPDATE-in-place every ~10 s.",
}


def upgrade() -> None:
    for table, comment in TABLE_COMMENTS.items():
        # Postgres ``COMMENT ON TABLE`` syntax. Single-quote escape via
        # the standard "double the apostrophe" pattern.
        escaped = comment.replace("'", "''")
        op.execute(f"COMMENT ON TABLE {table} IS '{escaped}'")


def downgrade() -> None:
    for table in TABLE_COMMENTS:
        op.execute(f"COMMENT ON TABLE {table} IS NULL")
