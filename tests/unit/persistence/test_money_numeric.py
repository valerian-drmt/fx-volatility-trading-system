"""Type hygiene (#326, audit 05#7 / 05#8): execution money columns are
``MoneyNumeric`` (exact in the database, float in Python); the policy covers
every money / price column on the execution tables; no naive datetime is
produced by the code paths the audit named."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from persistence import models
from persistence.models import Money, MoneyNumeric, Pct, Price

pytest.importorskip("pytest_asyncio")

MONEY_COLUMNS = {
    "trade_structure": {"total_premium_paid_usd", "total_slippage_usd", "total_commission_usd", "total_entry_cost_usd"},
    "trade_order": {"contract_strike", "limit_price", "preview_iv_pct", "preview_price", "avg_fill_price",
                    "total_commission_usd", "slippage_per_contract", "total_slippage_usd"},
    "trade_fill": {"fill_price", "commission_usd", "spot_at_fill", "bid_at_fill", "ask_at_fill", "iv_implied_from_fill"},
    "booked_position": {"entry_premium_usd", "entry_total_cost_usd", "entry_spot", "entry_iv_avg",
                        "exit_premium_usd", "exit_total_cost_usd", "gross_pnl_usd", "net_pnl_usd"},
    "spot_order": {"limit_price", "avg_fill_price", "commission_usd"},
    "hedge_order": {"fill_price", "commission_usd", "spread_paid_usd", "total_cost_usd"},
}


def test_every_execution_money_column_is_numeric() -> None:
    for table, cols in MONEY_COLUMNS.items():
        t = models.Base.metadata.tables[table]
        for col in cols:
            typ = t.c[col].type
            assert isinstance(typ, MoneyNumeric), f"{table}.{col} is {typ!r}, expected MoneyNumeric"


def test_decorator_binds_floats_exactly_and_reads_floats() -> None:
    assert Money.process_bind_param(2.37, None) == Decimal("2.37")
    assert Money.process_bind_param(Decimal("1.10"), None) == Decimal("1.10")
    assert Money.process_bind_param(3, None) == Decimal("3")
    assert Money.process_bind_param(None, None) is None
    assert Money.process_result_value(Decimal("0.01151105"), None) == 0.01151105
    assert Money.process_result_value(None, None) is None
    assert (Money.impl.precision, Money.impl.scale) == (15, 2)
    assert (Price.impl.precision, Price.impl.scale) == (18, 8)
    assert (Pct.impl.precision, Pct.impl.scale) == (10, 4)


@pytest.mark.asyncio
async def test_round_trip_on_sqlite(sqlite_sessionmaker) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from persistence.models import SpotOrder

    async with sqlite_sessionmaker() as db:
        db.add(SpotOrder(symbol="EUR", currency="USD", exchange="IDEALPRO", side="BUY", qty=25_000,
                         order_type="LMT", limit_price=1.12255, commission_usd=Decimal("2.37"), state="pending"))
        await db.commit()
    async with sqlite_sessionmaker() as db:
        row = (await db.execute(select(SpotOrder))).scalar_one()
    assert isinstance(row.limit_price, float) and row.limit_price == 1.12255
    assert isinstance(row.commission_usd, float) and row.commission_usd == 2.37


def test_migration_061_covers_the_same_columns() -> None:
    import importlib.util
    from pathlib import Path

    path = Path("src/persistence/migrations/versions/061_money_numeric.py")
    spec = importlib.util.spec_from_file_location("m061", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    migrated = {(t, c) for t, c, _ in mod.COLUMNS}
    declared = {(t, c) for t, cols in MONEY_COLUMNS.items() for c in cols}
    assert migrated == declared


def test_expiry_parsing_is_aware() -> None:
    from engines.execution.position_sync import _expiry_to_date

    assert _expiry_to_date("20261214") == datetime(2026, 12, 14, tzinfo=UTC).date()
    assert _expiry_to_date(None) is None
