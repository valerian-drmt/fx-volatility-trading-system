"""Unit tests for the core trading ORM models.

Exercises schema-level behavior (tablenames, columns, CHECK constraints,
JSONB roundtrip, relationships) without needing a real Postgres — we use
an async in-memory SQLite with JSON fallback for JSONB columns where
possible. CHECK constraint tests explicitly emulate the constraint via
SQLAlchemy Core to cover both sides.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from persistence.models import (
    AccountSnap,
    BacktestRun,
    Base,
    Position,
    PositionSnapshot,
    Signal,
    Trade,
    VolSurface,
)


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session

    await engine.dispose()


def test_tables_are_declared_with_expected_names():
    assert Position.__tablename__ == "positions"
    assert PositionSnapshot.__tablename__ == "position_snapshots"
    assert Trade.__tablename__ == "trades"
    assert AccountSnap.__tablename__ == "account_snaps"


def test_positions_has_expected_check_constraints():
    constraint_names = {c.name for c in Position.__table__.constraints if c.name}
    assert "ck_positions_instrument_type" in constraint_names
    assert "ck_positions_side" in constraint_names
    assert "ck_positions_option_type" in constraint_names
    assert "ck_positions_status" in constraint_names


def test_trades_has_unique_constraint_on_ib_order_id():
    constraint_names = {c.name for c in Trade.__table__.constraints if c.name}
    assert "uq_trades_ib_order_id" in constraint_names


def test_account_snaps_currencies_column_uses_jsonb_on_postgres():
    from sqlalchemy.dialects import postgresql, sqlite

    col = AccountSnap.__table__.c.currencies
    pg_impl = col.type.dialect_impl(postgresql.dialect())
    sqlite_impl = col.type.dialect_impl(sqlite.dialect())
    assert isinstance(pg_impl, JSONB), "postgres should compile to JSONB"
    assert not isinstance(sqlite_impl, JSONB), "sqlite must fall back to JSON"


def test_position_snapshot_foreign_key_to_positions():
    fks = list(PositionSnapshot.__table__.c.position_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "positions"


@pytest.mark.asyncio
async def test_position_insert_and_query(async_session):
    pos = Position(
        symbol="EUR.USD",
        instrument_type="SPOT",
        side="BUY",
        quantity=Decimal("100000"),
        entry_price=Decimal("1.08500000"),
        entry_timestamp=datetime(2026, 4, 17, 10, 0, tzinfo=UTC),
    )
    async_session.add(pos)
    await async_session.commit()

    result = await async_session.execute(select(Position))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].symbol == "EUR.USD"
    assert rows[0].status == "OPEN"
    assert rows[0].id is not None


@pytest.mark.asyncio
async def test_position_with_snapshots_relationship(async_session):
    pos = Position(
        symbol="EUR.USD",
        instrument_type="OPTION",
        side="BUY",
        quantity=Decimal("1"),
        strike=Decimal("1.08000"),
        maturity=date(2026, 5, 15),
        option_type="CALL",
        entry_price=Decimal("0.00500000"),
        entry_timestamp=datetime(2026, 4, 17, 10, 0, tzinfo=UTC),
    )
    pos.snapshots.append(
        PositionSnapshot(
            timestamp=datetime(2026, 4, 17, 11, 0, tzinfo=UTC),
            spot=Decimal("1.08500000"),
            iv=Decimal("7.35000"),
            delta_usd=Decimal("500.00"),
        )
    )
    async_session.add(pos)
    await async_session.commit()

    result = await async_session.execute(select(Position))
    loaded = result.scalar_one()
    assert len(loaded.snapshots) == 1
    assert loaded.snapshots[0].delta_usd == Decimal("500.00")
    assert loaded.snapshots[0].position is loaded


@pytest.mark.asyncio
async def test_trade_unique_constraint_on_ib_order_id(async_session):
    async_session.add(
        Trade(
            ib_order_id="IB-42",
            side="BUY",
            quantity=Decimal("1"),
            price=Decimal("1.08500000"),
            timestamp=datetime(2026, 4, 17, 10, 0, tzinfo=UTC),
        )
    )
    await async_session.commit()

    async_session.add(
        Trade(
            ib_order_id="IB-42",
            side="SELL",
            quantity=Decimal("1"),
            price=Decimal("1.08600000"),
            timestamp=datetime(2026, 4, 17, 11, 0, tzinfo=UTC),
        )
    )
    with pytest.raises(Exception) as excinfo:
        await async_session.commit()
    assert "UNIQUE" in str(excinfo.value).upper() or "IntegrityError" in type(
        excinfo.value
    ).__name__


@pytest.mark.asyncio
async def test_account_snap_currencies_jsonb_roundtrip(async_session):
    snap = AccountSnap(
        timestamp=datetime(2026, 4, 17, 10, 0, tzinfo=UTC),
        net_liq_usd=Decimal("125000.00"),
        currencies={"USD": 75000.50, "EUR": 45000.25, "GBP": 5000.00},
        open_positions_count=3,
    )
    async_session.add(snap)
    await async_session.commit()

    result = await async_session.execute(select(AccountSnap))
    loaded = result.scalar_one()
    assert loaded.currencies == {"USD": 75000.50, "EUR": 45000.25, "GBP": 5000.00}
    assert loaded.net_liq_usd == Decimal("125000.00")
    assert loaded.open_positions_count == 3


# --- R1 PR #4 : vol and analytics tables ------------------------------------


def test_vol_signal_backtest_tables_are_declared():
    assert VolSurface.__tablename__ == "vol_surfaces"
    assert Signal.__tablename__ == "signals"
    assert BacktestRun.__tablename__ == "backtest_runs"


def test_vol_surfaces_has_unique_on_timestamp_and_underlying():
    constraint_names = {c.name for c in VolSurface.__table__.constraints if c.name}
    assert "uq_vol_surfaces_ts_underlying" in constraint_names


def test_signals_has_unique_and_signal_type_check():
    constraint_names = {c.name for c in Signal.__table__.constraints if c.name}
    assert "uq_signals_ts_underlying_tenor" in constraint_names
    assert "ck_signals_signal_type" in constraint_names


def test_vol_surfaces_surface_data_is_not_null_jsonb_portable():
    col = VolSurface.__table__.c.surface_data
    assert col.nullable is False
    from sqlalchemy.dialects import postgresql

    pg_impl = col.type.dialect_impl(postgresql.dialect())
    assert isinstance(pg_impl, JSONB), "surface_data should compile to JSONB on postgres"


async def test_vol_surface_jsonb_roundtrip(async_session):
    surface_payload = {
        "1M": {"10dp": 8.33, "25dp": 7.30, "atm": 6.76, "25dc": 6.55, "10dc": 6.80},
        "2M": {"10dp": 8.10, "25dp": 7.20, "atm": 6.80, "25dc": 6.60, "10dc": 6.85},
    }
    fair_payload = {"1M": 7.55, "2M": 7.69}
    async_session.add(
        VolSurface(
            timestamp=datetime(2026, 4, 17, 15, 0, tzinfo=UTC),
            underlying="EUR.USD",
            spot=Decimal("1.08500000"),
            forward=Decimal("1.08700000"),
            surface_data=surface_payload,
            fair_vol_data=fair_payload,
            scan_duration_s=Decimal("2.45"),
        )
    )
    await async_session.commit()

    result = await async_session.execute(select(VolSurface))
    loaded = result.scalar_one()
    assert loaded.surface_data == surface_payload
    assert loaded.fair_vol_data == fair_payload
    assert loaded.surface_data["1M"]["atm"] == 6.76


async def test_vol_surface_unique_constraint_on_ts_underlying(async_session):
    ts = datetime(2026, 4, 17, 15, 0, tzinfo=UTC)
    async_session.add(
        VolSurface(
            timestamp=ts,
            underlying="EUR.USD",
            spot=Decimal("1.08500000"),
            surface_data={"1M": {"atm": 6.76}},
        )
    )
    await async_session.commit()

    async_session.add(
        VolSurface(
            timestamp=ts,
            underlying="EUR.USD",
            spot=Decimal("1.08510000"),
            surface_data={"1M": {"atm": 6.80}},
        )
    )
    with pytest.raises(Exception) as excinfo:
        await async_session.commit()
    assert "UNIQUE" in str(excinfo.value).upper() or "IntegrityError" in type(
        excinfo.value
    ).__name__


async def test_signal_roundtrip_and_type(async_session):
    async_session.add(
        Signal(
            timestamp=datetime(2026, 4, 17, 15, 0, tzinfo=UTC),
            underlying="EUR.USD",
            tenor="1M",
            dte=30,
            sigma_mid=Decimal("6.76000"),
            sigma_fair=Decimal("7.55000"),
            ecart=Decimal("0.79000"),
            signal_type="CHEAP",
            rv=Decimal("7.10000"),
        )
    )
    await async_session.commit()

    result = await async_session.execute(select(Signal))
    loaded = result.scalar_one()
    assert loaded.tenor == "1M"
    assert loaded.signal_type == "CHEAP"
    assert loaded.ecart == Decimal("0.79000")


async def test_backtest_run_jsonb_and_metrics_roundtrip(async_session):
    parameters = {"threshold_bps": 20, "tenor": "1M", "symbol": "EUR.USD"}
    equity_curve = [
        {"date": "2025-01-01", "equity": 100000.0},
        {"date": "2025-12-31", "equity": 115200.0},
    ]
    from datetime import date as _date

    async_session.add(
        BacktestRun(
            strategy_name="vol_rv_spread_v1",
            parameters=parameters,
            start_date=_date(2025, 1, 1),
            end_date=_date(2025, 12, 31),
            sharpe_ratio=Decimal("1.4523"),
            max_drawdown_pct=Decimal("5.8100"),
            hit_rate=Decimal("0.5833"),
            total_return_pct=Decimal("15.2000"),
            n_trades=12,
            equity_curve=equity_curve,
        )
    )
    await async_session.commit()

    result = await async_session.execute(select(BacktestRun))
    loaded = result.scalar_one()
    assert loaded.parameters == parameters
    assert loaded.equity_curve == equity_curve
    assert loaded.sharpe_ratio == Decimal("1.4523")
    assert loaded.n_trades == 12
    assert loaded.created_at is not None  # server_default
