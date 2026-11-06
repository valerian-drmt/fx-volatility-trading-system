"""The test harness itself (#338): FKs really are on, the clock really freezes,
the canonical payloads match what the publishers emit, the IB factories have
the shapes the handlers consume.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from tests.fixtures.payloads import NAMES, payload


async def test_sqlite_fixture_enforces_foreign_keys(sqlite_sessionmaker: Any) -> None:
    """T7-03: a fill for a missing order must be refused like Postgres would."""
    from persistence.models import StructureFill

    async with sqlite_sessionmaker() as db:
        db.add(StructureFill(order_id=999_999, ib_execution_id="orphan", timestamp=datetime.now(UTC),
                             qty_filled=1, fill_price=1.0, commission_usd=0.0, side="BUY"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_no_fk_fixture_is_an_explicit_opt_out(sqlite_sessionmaker_no_fk: Any) -> None:
    from persistence.models import StructureFill

    async with sqlite_sessionmaker_no_fk() as db:
        db.add(StructureFill(order_id=999_999, ib_execution_id="orphan2", timestamp=datetime.now(UTC),
                             qty_filled=1, fill_price=1.0, commission_usd=0.0, side="BUY"))
        await db.commit()  # accepted: the caller asked for no FK enforcement


def test_frozen_clock_freezes_now(frozen_clock: datetime) -> None:
    assert datetime.now(UTC) == frozen_clock
    assert datetime.now(UTC).isoformat().startswith("2026-11-06T12:00:00")


@pytest.mark.parametrize("name", NAMES)
def test_every_canonical_payload_loads_and_is_a_fresh_copy(name: str) -> None:
    a, b = payload(name), payload(name)
    assert isinstance(a, dict) and a
    a["__mutated__"] = True
    assert "__mutated__" not in b


class _RecordingRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.sets: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets[key] = value

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


async def test_redis_payload_fixtures_match_the_publishers() -> None:
    """The fixture is the contract: the keys the publisher emits must be the
    keys the fixture carries (so a consumer test built on the fixture sees what
    production sees)."""
    from bus import publisher

    publisher.reset_throttle_for_tests()
    r = _RecordingRedis()
    t = payload("ticks")
    await publisher.publish_tick(r, t["symbol"], t["bid"], t["ask"], t["mid"], timestamp=datetime(2026, 11, 6, 12, tzinfo=UTC))
    sent = json.loads(r.published[-1][1])
    assert set(sent) == set(t) and r.published[-1][0] == "ticks"

    a = payload("account")
    await publisher.publish_account(r, {k: v for k, v in a.items() if k != "timestamp"})
    assert set(json.loads(r.published[-1][1])) == set(a) and r.published[-1][0] == "account"

    g = payload("risk_update")
    await publisher.publish_risk_update(r, greeks=g["greeks"], pnl_curve=None)
    assert set(json.loads(r.published[-1][1])) == set(g) and r.published[-1][0] == "risk_update"

    o = payload("orders")
    await publisher.publish_order_event(r, o["structure_id"], {k: v for k, v in o.items() if k not in ("structure_id", "timestamp")})
    ch, body = r.published[-1]
    assert ch == f"orders:{o['structure_id']}"
    assert set(json.loads(body)) <= set(o) | {"structure_id", "timestamp"}

    p = payload("positions")
    await publisher.publish_position_update(r, {k: v for k, v in p.items() if k != "timestamp"})
    assert set(json.loads(r.published[-1][1])) == set(p) and r.published[-1][0] == "positions"

    frame = payload("db_events")
    assert set(frame) == {"table", "payload"}  # what engines.db_writer.service._enqueue parses


async def test_ib_factories_have_the_shapes_the_handlers_consume(ib_factories: Any) -> None:
    from engines.execution.fills_handler import attach_fill_handlers
    from engines.execution.ib_trade_log import ib_reason_from_trade, ib_why_held

    t = ib_factories.fake_trade(status="Inactive", error="Error 201, reqId 7: Order rejected - reason: x")
    assert ib_reason_from_trade(t).startswith("Error 201")
    assert ib_why_held(ib_factories.fake_trade(why_held="locate")) == "locate"
    # the realistic fill: empty commission report at fill time (EX-03)
    f = ib_factories.fake_fill()
    assert f.commissionReport.commission == 0.0 and f.commissionReport.currency == ""
    assert ib_factories.fake_fill(commission=2.37).commissionReport.commission == 2.37
    # attach wires the three events of a fake trade
    attach_fill_handlers(trade=t, order_id=1, sessionmaker_factory=None)  # type: ignore[arg-type]
    assert (len(t.statusEvent.handlers), len(t.fillEvent.handlers), len(t.commissionReportEvent.handlers)) == (1, 1, 1)
    # FakeIB records the orderRef the placement helper stamps
    ib = ib_factories.FakeIB()
    tr = ib.placeOrder(ib_factories.fake_contract(), SimpleNamespace(action="BUY", totalQuantity=1, lmtPrice=1.0, orderRef="fxvol:1:2"))
    assert tr.order.orderRef == "fxvol:1:2" and ib.placed
