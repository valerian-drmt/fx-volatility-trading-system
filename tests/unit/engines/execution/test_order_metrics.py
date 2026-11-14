"""#495: what the order path reports about itself."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from prometheus_client import REGISTRY

from api.orchestration.pretrade_gate import count_gate_decision
from engines.execution.ib_heartbeat import mark_loop_ran, publish_waiting_orders, waiting_orders
from persistence.models import StructureOrder, TradeStructure

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


async def _order(db: Any, s: TradeStructure, idx: int, state: str, *, age_s: float) -> None:
    then = NOW - timedelta(seconds=age_s)
    db.add(StructureOrder(
        structure_id=s.id, leg_idx=idx, order_role="entry", contract_type="call", side="BUY", qty=1,
        order_type="LMT", limit_price=0.01, state=state,
        submitted_at=None if state == "pending" else then, state_updated_at=then,
    ))


async def test_waiting_orders_counts_and_ages_by_state(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=1,
                           state="submitted", execution_mode="live")
        db.add(s)
        await db.flush()
        await _order(db, s, 0, "pending", age_s=900)
        await _order(db, s, 1, "submitted", age_s=60)
        await _order(db, s, 2, "submitted", age_s=7200)
        await _order(db, s, 3, "filled", age_s=99999)       # ended: not waiting
        await db.commit()
        w = await waiting_orders(db, NOW)
        assert w["pending"] == (1, 900.0)
        assert w["submitted"] == (2, 7200.0)
        assert w["partially_filled"] == (0, 0.0)

        await publish_waiting_orders(db, NOW)
    assert REGISTRY.get_sample_value("orders_waiting", {"state": "submitted"}) == 2
    assert REGISTRY.get_sample_value("orders_oldest_waiting_seconds", {"state": "pending"}) == 900
    # a state with nothing waiting reads 0, so its alert resolves
    assert REGISTRY.get_sample_value("orders_oldest_waiting_seconds", {"state": "partially_filled"}) == 0


def test_the_gate_counts_its_verdicts_and_each_reason() -> None:
    def val(name: str, labels: dict[str, str]) -> float:
        return REGISTRY.get_sample_value(name, labels) or 0.0

    passed = val("order_gate_decisions_total", {"outcome": "passed"})
    refused = val("order_gate_decisions_total", {"outcome": "refused"})
    stale = val("order_refusals_total", {"reason": "surface_stale"})
    halted = val("order_refusals_total", {"reason": "trading_halted"})
    count_gate_decision([])
    count_gate_decision(["surface_stale", "trading_halted"])
    assert val("order_gate_decisions_total", {"outcome": "passed"}) == passed + 1
    assert val("order_gate_decisions_total", {"outcome": "refused"}) == refused + 1
    assert val("order_refusals_total", {"reason": "surface_stale"}) == stale + 1
    assert val("order_refusals_total", {"reason": "trading_halted"}) == halted + 1


def test_a_loop_stamps_its_last_pass() -> None:
    before = datetime.now(UTC).timestamp()
    mark_loop_ran("reaper")
    stamped = REGISTRY.get_sample_value("order_loop_last_run_timestamp_seconds", {"loop": "reaper"})
    assert stamped is not None and stamped >= before
