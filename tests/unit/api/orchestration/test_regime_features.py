"""End-to-end test for api.orchestration.regime_features.build_features_payload.

Boots an in-memory aiosqlite DB, seeds regime_snapshots + regime_lookup_table
+ vol_features_context_baseline, calls the orchestrator, and checks the
resulting payload structure (features array + synthesis row).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


def _coerce_bigint_to_integer(metadata) -> None:
    from sqlalchemy import BigInteger, Integer
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()


async def _make_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base
    _coerce_bigint_to_integer(Base.metadata)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _seed_history(db, *, symbol="EURUSD", n_history=40) -> datetime:
    """Seed n_history regime_snapshots ending at NOW. Returns the latest ts."""
    from persistence.models import RegimeSnapshot
    base = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    ts_latest = base
    for i in range(n_history):
        ts = base - timedelta(hours=n_history - 1 - i)
        # Vary z so history is meaningful for bucket / pct / delta_z_1h.
        z_l = -0.5 + (i / n_history) * 1.0
        z_v = (i % 7 - 3) / 5.0
        z_s = -2.07 if i == n_history - 1 else (i % 11 - 5) / 6.0
        db.add(RegimeSnapshot(
            timestamp=ts, symbol=symbol, label="calm", method="threshold_heuristic",
            vol_level_pct=Decimal(f"{6.0 + (i % 7) / 10:.4f}"),
            vol_of_vol_pct=Decimal(f"{0.18 + (i % 5) / 100:.4f}"),
            term_slope_pct=Decimal(f"{0.08 - (i % 9) / 100:.4f}"),
            vol_level_z=Decimal(f"{z_l:.4f}"),
            vol_of_vol_z=Decimal(f"{z_v:.4f}"),
            term_slope_z=Decimal(f"{z_s:.4f}"),
            event_dampener=False, days_to_next_event=Decimal("7.5"),
            next_event_type="none",
        ))
        ts_latest = ts
    return ts_latest


# Regime pattern lookup is no longer DB-seeded — it lives in
# core.regime_patterns.REGIME_PATTERNS (loaded at import, migration 042
# dropped the regime_pattern_dict mirror). No fixture seeding needed.


async def test_build_features_payload_full_shape():
    from api.orchestration.regime_features import build_features_payload

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_history(db)
            await db.commit()
        async with maker() as db:
            payload = await build_features_payload(db, symbol="EURUSD")
        assert payload is not None
        # Top-level keys
        assert {"timestamp", "symbol", "features", "synthesis"} <= payload.keys()
        # 3 feature rows
        names = [f["name"] for f in payload["features"]]
        assert names == ["vol_level", "vol_of_vol", "term_slope"]
        for f in payload["features"]:
            assert {"name", "value", "z", "bucket", "delta_z_1h", "pct", "signal", "expected_z"} <= f.keys()
        # Synthesis row
        synth = payload["synthesis"]
        assert {"joint_pattern", "regime", "dominant", "vs_expected", "action"} <= synth.keys()
        assert synth["joint_pattern"] is not None
        # latest term_slope_z = -2.07 → bucket should be "--" or "-" → joint_pattern ends with - or --.
        assert synth["joint_pattern"].endswith(",-)") or synth["joint_pattern"].endswith(",--)")
        # Action format : "size × 1.0 + monitor" (no dampener, no critical pattern)
        assert "size ×" in synth["action"]
    finally:
        await engine.dispose()


async def test_build_features_payload_returns_none_when_empty():
    from api.orchestration.regime_features import build_features_payload

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            assert await build_features_payload(db) is None
    finally:
        await engine.dispose()


async def test_dominant_feature_is_argmax_abs_z():
    from api.orchestration.regime_features import build_features_payload

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_history(db)
            await db.commit()
        async with maker() as db:
            payload = await build_features_payload(db, symbol="EURUSD")
        assert payload is not None
        # Latest row : term_slope_z = -2.07 (largest |z|).
        assert payload["synthesis"]["dominant"] == "term_slope"
    finally:
        await engine.dispose()


async def test_action_includes_dampener_modifier():
    """When event_dampener=True on the latest row, action starts with size × 0.5."""
    from api.orchestration.regime_features import build_features_payload
    from persistence.models import RegimeSnapshot

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            await _seed_history(db)
            # Override : flip event_dampener on the latest row.
            from sqlalchemy import desc, select, update
            row_id = (await db.execute(
                select(RegimeSnapshot.id)
                .order_by(desc(RegimeSnapshot.timestamp)).limit(1)
            )).scalar_one()
            await db.execute(
                update(RegimeSnapshot).where(RegimeSnapshot.id == row_id)
                .values(event_dampener=True)
            )
            await db.commit()
        async with maker() as db:
            payload = await build_features_payload(db, symbol="EURUSD")
        assert payload is not None
        assert payload["synthesis"]["action"].startswith("size × 0.5")
    finally:
        await engine.dispose()
