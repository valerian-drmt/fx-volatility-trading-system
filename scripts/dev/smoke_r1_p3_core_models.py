"""Functional smoke test of the R1 PR #3 core trading ORM models.

Runs end-to-end scenarios against a real Postgres (via docker-compose.dev.yml)
and prints the observed behavior. Each step is a user-visible functional check
of what this PR actually delivers:

    1. Insert a Position, verify default status = 'OPEN' and created_at populated
    2. Update the Position, verify updated_at refreshes automatically (onupdate)
    3. Add PositionSnapshots via the relationship, verify roundtrip
    4. Delete the Position, verify cascade = orphan snapshots are deleted too
    5. Insert a Trade with ib_order_id='IB-TEST', verify a duplicate raises
       IntegrityError (UNIQUE constraint at the DB level)
    6. Insert an AccountSnap with a JSONB currencies dict, verify roundtrip
       and that Postgres ->> operator works (proves column is JSONB not TEXT)

Usage (PowerShell):
    docker compose -f docker-compose.dev.yml up -d postgres
    $env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5432/fxvol"
    python scripts/dev/db_create_tables.py       # prerequisite
    python scripts/dev/smoke_r1_p3_core_models.py
    python scripts/dev/db_drop_tables.py         # cleanup

Expected exit code : 0 on success, non-zero if any assertion fails.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from persistence.db import get_session  # noqa: E402
from persistence.models import (  # noqa: E402
    AccountSnap,
    Position,
    PositionSnapshot,
    Trade,
)


def _banner(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


async def step_1_insert_position() -> int:
    _banner("STEP 1 : Insert Position, check default status + created_at")
    async with get_session() as s:
        pos = Position(
            symbol="EUR.USD",
            instrument_type="SPOT",
            side="BUY",
            quantity=Decimal("100000"),
            entry_price=Decimal("1.08500000"),
            entry_timestamp=datetime.now(UTC),
        )
        s.add(pos)
    async with get_session() as s:
        result = await s.execute(select(Position).where(Position.symbol == "EUR.USD"))
        pos = result.scalar_one()
        assert pos.status == "OPEN", f"default status should be OPEN, got {pos.status}"
        assert pos.created_at is not None, "created_at must be auto-populated"
        assert pos.updated_at is not None, "updated_at must be auto-populated"
        print(f"  OK : id={pos.id}, status={pos.status}, created_at={pos.created_at}")
        return pos.id


async def step_2_update_refreshes_updated_at(pos_id: int) -> None:
    _banner("STEP 2 : Update Position, check updated_at auto-refreshes")
    async with get_session() as s:
        pos = (await s.execute(select(Position).where(Position.id == pos_id))).scalar_one()
        before = pos.updated_at
        print(f"  updated_at BEFORE : {before}")
    await asyncio.sleep(1)  # ensure clock moves
    async with get_session() as s:
        pos = (await s.execute(select(Position).where(Position.id == pos_id))).scalar_one()
        pos.status = "CLOSED"
        pos.exit_price = Decimal("1.08750000")
        pos.exit_timestamp = datetime.now(UTC)
    async with get_session() as s:
        pos = (await s.execute(select(Position).where(Position.id == pos_id))).scalar_one()
        after = pos.updated_at
        print(f"  updated_at AFTER  : {after}")
        assert after > before, "updated_at must refresh on UPDATE"
        assert pos.status == "CLOSED"
        print(f"  OK : status={pos.status}, updated_at moved forward")


async def step_3_snapshots_relationship() -> int:
    _banner("STEP 3 : Position with snapshots via relationship, check roundtrip")
    async with get_session() as s:
        pos = Position(
            symbol="USD.JPY",
            instrument_type="OPTION",
            side="BUY",
            quantity=Decimal("1"),
            strike=Decimal("150.00000"),
            option_type="CALL",
            entry_price=Decimal("0.00500000"),
            entry_timestamp=datetime.now(UTC),
        )
        pos.snapshots.append(
            PositionSnapshot(
                timestamp=datetime.now(UTC),
                spot=Decimal("149.50000000"),
                delta_usd=Decimal("450.00"),
            )
        )
        pos.snapshots.append(
            PositionSnapshot(
                timestamp=datetime.now(UTC),
                spot=Decimal("149.80000000"),
                delta_usd=Decimal("480.00"),
            )
        )
        s.add(pos)
    async with get_session() as s:
        pos = (
            await s.execute(
                select(Position)
                .where(Position.symbol == "USD.JPY")
                .options(selectinload(Position.snapshots))
            )
        ).scalar_one()
        assert len(pos.snapshots) == 2, f"expected 2 snapshots, got {len(pos.snapshots)}"
        print(f"  OK : position {pos.id} has {len(pos.snapshots)} snapshots")
        for snap in pos.snapshots:
            print(f"       snap id={snap.id}, delta_usd={snap.delta_usd}")
        return pos.id


async def step_4_cascade_delete(pos_id: int) -> None:
    _banner("STEP 4 : Delete Position, check snapshots cascade-deleted")
    async with get_session() as s:
        pos = (
            await s.execute(
                select(Position)
                .where(Position.id == pos_id)
                .options(selectinload(Position.snapshots))
            )
        ).scalar_one()
        snaps_ids = [snap.id for snap in pos.snapshots]
        print(f"  snap ids before delete : {snaps_ids}")
        await s.delete(pos)
    async with get_session() as s:
        remaining = (
            await s.execute(
                select(PositionSnapshot).where(PositionSnapshot.position_id == pos_id)
            )
        ).scalars().all()
        assert len(remaining) == 0, f"expected 0 snapshots, got {len(remaining)}"
        print(f"  OK : snapshots after delete = {len(remaining)}")


async def step_5_unique_constraint_on_ib_order_id() -> None:
    _banner("STEP 5 : Trade UNIQUE constraint on ib_order_id")
    async with get_session() as s:
        s.add(
            Trade(
                ib_order_id="IB-TEST-UNIQUE",
                side="BUY",
                quantity=Decimal("1"),
                price=Decimal("1.08500000"),
                timestamp=datetime.now(UTC),
            )
        )
    print("  first Trade with ib_order_id='IB-TEST-UNIQUE' inserted OK")
    raised = False
    try:
        async with get_session() as s:
            s.add(
                Trade(
                    ib_order_id="IB-TEST-UNIQUE",
                    side="SELL",
                    quantity=Decimal("1"),
                    price=Decimal("1.08600000"),
                    timestamp=datetime.now(UTC),
                )
            )
    except IntegrityError as exc:
        raised = True
        print(f"  OK : duplicate rejected with IntegrityError ({exc.orig!r})")
    assert raised, "duplicate ib_order_id should have raised IntegrityError"


async def step_6_jsonb_roundtrip_and_operator() -> None:
    _banner("STEP 6 : AccountSnap JSONB roundtrip + Postgres ->> operator")
    async with get_session() as s:
        s.add(
            AccountSnap(
                timestamp=datetime.now(UTC),
                net_liq_usd=Decimal("125000.00"),
                currencies={"USD": 75000.50, "EUR": 45000.25, "GBP": 5000.00},
                open_positions_count=3,
            )
        )
    async with get_session() as s:
        snap = (await s.execute(select(AccountSnap))).scalars().all()[-1]
        assert snap.currencies == {"USD": 75000.50, "EUR": 45000.25, "GBP": 5000.00}
        print(f"  OK : roundtrip currencies = {snap.currencies}")

        # Native JSONB operator: only works if column is JSONB, fails if TEXT.
        result = await s.execute(
            text("SELECT currencies->>'USD' AS usd FROM account_snaps ORDER BY id DESC LIMIT 1")
        )
        usd = result.scalar_one()
        print(f"  OK : Postgres `currencies->>'USD'` returned {usd!r}")
        assert usd == "75000.5", f"expected '75000.5', got {usd!r}"


async def main() -> None:
    pos_id = await step_1_insert_position()
    await step_2_update_refreshes_updated_at(pos_id)
    pos2_id = await step_3_snapshots_relationship()
    await step_4_cascade_delete(pos2_id)
    await step_5_unique_constraint_on_ib_order_id()
    await step_6_jsonb_roundtrip_and_operator()
    print()
    print("=" * 70)
    print("  ALL 6 FUNCTIONAL CHECKS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
