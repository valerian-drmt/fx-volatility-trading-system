"""Schema drift gate, the half that runs without Postgres (#325).

The real gate is ``alembic check`` in CI (ci.yml, live-integration job). Here:
the indexes and uniques the migrations created are declared in the models by
the same names, the partial unique on pending hedges is there, the CI job
carries the check + the round trip, and the metadata builds on SQLite.
"""
from __future__ import annotations

from pathlib import Path

from persistence import models

ROOT = Path(__file__).resolve().parents[3]

EXPECTED_INDEXES = {
    ("open_position", "ix_positions_structure_unique", True),
    # the first run of the gate on CI listed these as present in Postgres and
    # absent from the models (#325, PR #393): declared since.
    ("book_state_snapshot_history", "ix_book_state_current", True),
    ("booked_position", "ix_trade_positions_ib_reconciled_at", False),
    ("booked_position", "ix_trade_positions_state", False),
    ("config_scalar", "ix_app_config_scalar_ns_active", False),
    ("config_vol_engine", "idx_vol_config_version_desc", False),
    ("db_writer_dead_letter", "ix_db_writer_dead_letter_created", False),
    ("event_calendar", "ix_events_event_hash", False),
    ("event_calendar", "ix_events_scheduled_at", False),
    ("feature_history", "ix_feature_history_symbol_ts", False),
    ("pca_model", "ix_pca_models_active_unique", True),
    ("pca_signal_history", "ix_pca_signals_symbol_ts", False),
    ("pca_surface_snapshot_history", "ix_surface_snap_hourly_symbol_ts", False),
    ("reconciliation_break", "ix_reconciliation_break_open", False),
    ("regime_snapshot_history", "ix_regime_snapshots_symbol_ts", False),
    ("regime_snapshot_history", "ix_regime_snapshots_timestamp", False),
    ("trade_event", "ix_trade_event_structure", False),
    ("trade_event", "ix_trade_event_ts", False),
    ("trade_event", "ix_trade_event_type_ts", False),
    ("trade_fill", "ix_structure_fills_order", False),
    ("trade_order", "ix_structure_orders_structure", False),
    ("trade_preview", "ix_trade_previews_created", False),
    ("trade_preview", "ix_trade_previews_state", False),
    ("trade_structure", "ix_trade_structures_state", False),
    ("open_position_history", "idx_open_position_history_position_ts", False),
    ("open_position_history", "idx_open_position_history_ts", False),
    ("vol_surface_history", "ix_vol_surface_underlying_ts", False),
    ("pca_signal_history", "ix_pca_signal_symbol_pc_ts", False),
    ("booked_position_metric_history", "ix_mtm_position_ts", False),
    ("hedge_order", "ix_hedge_position_ts", False),
    ("hedge_order", "uq_hedge_order_pending_per_position", True),
    ("exit_alert", "ix_exit_alerts_position_ts", False),
    ("account_history", "ix_account_history_ts", False),
}
EXPECTED_UNIQUES = {
    ("regime_snapshot_history", "uq_regime_snapshot_symbol_ts"),
    ("trade_structure", None),  # preview_id unique=True on the column (053)
    ("portfolio_daily", "uq_portfolio_daily_day"),
    ("pnl_attribution_latest", "uq_pnl_attribution_latest_lookback"),
}


def _indexes() -> set[tuple[str, str, bool]]:
    out = set()
    for table in models.Base.metadata.tables.values():
        for ix in table.indexes:
            out.add((table.name, ix.name, bool(ix.unique)))
    return out


def test_migration_created_indexes_are_declared_in_the_models() -> None:
    missing = EXPECTED_INDEXES - _indexes()
    assert not missing, f"declare these in models.py so alembic check agrees: {sorted(missing)}"


def test_pending_hedge_unique_is_partial_on_state() -> None:
    table = models.Base.metadata.tables["hedge_order"]
    ix = next(i for i in table.indexes if i.name == "uq_hedge_order_pending_per_position")
    assert ix.unique and [c.name for c in ix.columns] == ["position_id"]
    assert "state = 'pending'" in str(ix.dialect_options["postgresql"]["where"])


def test_unique_constraints_declared() -> None:
    for table_name, uq_name in EXPECTED_UNIQUES:
        table = models.Base.metadata.tables[table_name]
        if uq_name is None:
            assert table.c["preview_id"].unique
            continue
        names = {c.name for c in table.constraints}
        assert uq_name in names, (table_name, uq_name)


def test_ci_runs_the_drift_gate_and_the_round_trip() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "alembic.ini check" in ci, "alembic check missing from the live-integration job"
    assert 'downgrade "-${ALEMBIC_ROUNDTRIP_STEPS}"' in ci and "ALEMBIC_ROUNDTRIP_STEPS" in ci
    env = (ROOT / "src" / "persistence" / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "compare_type=True" in env


def test_metadata_builds_on_sqlite(sqlite_sessionmaker) -> None:  # type: ignore[no-untyped-def]
    """The shared harness ran ``create_all`` with every declared index
    (DESC expressions, the partial unique) without raising."""
    assert sqlite_sessionmaker is not None


def test_table_comments_match_migration_042() -> None:
    import importlib.util

    path = ROOT / "src" / "persistence" / "migrations" / "versions" / "042_add_table_comments.py"
    spec = importlib.util.spec_from_file_location("m042", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert models.TABLE_COMMENTS == mod.TABLE_COMMENTS
    for name, comment in models.TABLE_COMMENTS.items():
        assert models.Base.metadata.tables[name].comment == comment, name


def test_dead_letter_error_is_text_like_the_migration() -> None:
    from sqlalchemy import Text

    assert isinstance(models.Base.metadata.tables["db_writer_dead_letter"].c["error"].type, Text)
